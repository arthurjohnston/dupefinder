"""Step 4.5: an AI agent reads each full-text-confirmed pair before a human does.

For every pair in gi.sqlite3's fulltext table with real shared text (by default only pairs with no
human verdict yet), builds an evidence packet -- both papers' metadata, their opening text (where
the bylines are), the full-text and General Index overlap figures, and the longest shared
passages -- and asks Claude for a structured verdict: what kind of overlap this is, which paper
looks earlier, how confident, the reasons, and what a human should check.

The agent's verdict goes in its own table, agent_reviews. **It never writes to `reviews`**, the
human table, and a human verdict always wins:
  - pairs with a row in `reviews` are skipped unless --include-reviewed;
  - the to_review_agent view lists pairs still awaiting a human, with the agent's latest verdict
    beside them, and drops a pair as soon as a human reviews it;
  - nothing else in the pipeline reads agent_reviews.
An agent verdict is a triage aid, not a finding.

Follows REVIEWING.md ("What to actually check, per candidate" and "Agent review mode"):
  - its own vocabulary: each verdict is stored with its REVIEWING.md agent equivalent
    (agent_decided_dupe / _false_positive / _boilerplate / _citation / _unsure, or the p / a
    corrections), never as a human status;
  - authorship from the PDFs (item 6, non-optional step 1): the packet carries pdftotext's page 1 of
    both retrieved PDFs, and the agent must list each byline's names;
  - every name compared against every name, initials included (non-optional step 2): shared_names
    and name_comparison are required output;
  - whole-document comparison (item 9, step 3): the overlap figures and passages come from
    compare_two_papers.find_shingle_matches over both complete documents;
  - title matches content (item 10): opening + middle text per paper, the wrong-PDF check, and a
    required title_matches_content answer per paper;
  - third-party quotations (item 11): the 60 words before each shared passage in both papers, and a
    quotation-mark flag;
  - boilerplate (item 4): each passage run through classify_dupes.classify_text (TEXT_PATTERNS +
    fallbacks), and new recurring shapes reported back in possible_new_text_patterns, not added;
  - later cites earlier (item 7): build_dupe_candidates.cites() over each paper's references.
The non-optional steps are enforced, not only asked for: a likely_copying verdict whose bylines
weren't both read, that lists a shared name, or with a title/content mismatch is stored as unsure,
the failure named in precondition_failures and the model's own answer kept in raw_verdict.

Each run is keyed by --prompt-version, so changing the prompt adds rows rather than overwriting old
ones; rerunning the same version skips pairs it already has unless --redo.

Cost control: --max-cost stops before the next call once the run has spent that much (estimated from
response.usage at the model's list price); --dry-run builds packets and counts tokens with
messages.count_tokens, making no billable call.

Credentials: the Anthropic SDK's usual resolution (ANTHROPIC_API_KEY, or an `ant auth login`
profile), else the key file ~/.config/dupefinder/anthropic_api_key (outside the repo, mode 600).

Usage: gi_agent_review.py [--db data/gi.sqlite3] [--limit N] [--max-cost 10] [--dry-run]
                          [--model claude-opus-5-5] [--effort high] [--prompt-version v1]
                          [--include-reviewed] [--redo]
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import compare_two_papers as ctp  # noqa: E402
import classify_dupes  # noqa: E402  (TEXT_PATTERNS + fallbacks: REVIEWING.md checklist item 4)
from build_dupe_candidates import cites  # noqa: E402  (later-cites-earlier heuristic, item 7)
import subprocess  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
KEY_FILE = Path.home() / ".config" / "dupefinder" / "anthropic_api_key"
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-4-5": (1.00, 5.00)}
OPENING_WORDS = 400      # per paper: enough for title, byline, affiliations, abstract
TOP_RUNS = 6             # longest shared passages quoted
RUN_WORDS = 120          # words quoted per passage
CONTEXT_WORDS = 60       # words shown before each passage (third-party quotation check, item 11)
PDF_PAGE1_CHARS = 3000   # first page of each PDF as pdftotext reads it (byline check, item 6)
# --packet: the sizes above ("full", what Claude gets), or "slim" for a local model on the CPU, where every
# prompt token costs ~25 ms (gpt-oss-20b, 2026-10-14): fewer and shorter passages, and a shorter opening
# text, which mostly repeats pdf_page1 (the byline is read from pdf_page1 either way).
PACKETS = {
    "full": dict(opening=OPENING_WORDS, middle=150, runs=TOP_RUNS, run_words=RUN_WORDS, context=CONTEXT_WORDS,
                 page1=PDF_PAGE1_CHARS),
    "slim": dict(opening=150, middle=100, runs=4, run_words=80, context=40, page1=2500),
}

# The kind of overlap. The same vocabulary as reviews.type, the human column.
TYPES = ["cross-author copying", "data copying", "duplicate publication", "text recycling", "same paper", "legitimate reuse",
         "shared third source", "boilerplate", "shared citations", "artifact", "unsure"]
PROBLEM_TYPES = {"cross-author copying", "data copying", "duplicate publication", "text recycling"}
DECISIONS = ["problem", "no problem", "unsure"]
SEVERITIES = ["none", "low", "medium", "high"]
VERDICTS = TYPES  # agent_reviews.verdict keeps the type, for queries written against v1/v2
# REVIEWING.md's agent vocabulary (never the human one): what each type corresponds to there.
REVIEWING_MD = {"cross-author copying": "agent_decided_dupe", "data copying": "agent_decided_dupe",
                "duplicate publication": "same_author (a)",
                "text recycling": "same_author (a)", "same paper": "same_paper (p)",
                "legitimate reuse": "agent_decided_false_positive",
                "shared third source": "agent_decided_false_positive", "boilerplate": "agent_decided_boilerplate",
                "shared citations": "agent_decided_citation", "artifact": "agent_decided_false_positive",
                "unsure": "agent_decided_unsure"}
SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": DECISIONS},
        "type": {"type": "string", "enum": TYPES},
        "severity": {"type": "string", "enum": SEVERITIES},
        "severity_reason": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "earlier_paper": {"type": "string", "enum": ["A", "B", "unclear"]},
        "summary": {"type": "string"},
        "byline_names_A": {"type": "array", "items": {"type": "string"}},
        "byline_names_B": {"type": "array", "items": {"type": "string"}},
        "shared_names": {"type": "array", "items": {"type": "string"}},
        "name_comparison": {"type": "string"},
        "title_matches_content_A": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "title_matches_content_B": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "later_cites_earlier": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "third_party_quotation_passages": {"type": "array", "items": {"type": "integer"}},
        "boilerplate_passages": {"type": "array", "items": {"type": "integer"}},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "checks_for_human": {"type": "array", "items": {"type": "string"}},
        "possible_new_text_patterns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["decision", "type", "severity", "severity_reason", "confidence", "earlier_paper", "summary", "byline_names_A", "byline_names_B",
                 "shared_names", "name_comparison", "title_matches_content_A", "title_matches_content_B",
                 "later_cites_earlier", "third_party_quotation_passages", "boilerplate_passages", "reasons",
                 "red_flags", "checks_for_human", "possible_new_text_patterns"],
    "additionalProperties": False,
}

SYSTEM = """You assist a research-integrity reviewer, following this project's REVIEWING.md runbook.
You are shown two academic papers that share a substantial amount of verbatim text, measured by exact
multi-word matching across both complete documents (compare_two_papers.py's word-shingle matcher).
Your job is triage before a human reads the pair: say what kind of overlap this most likely is, and
what the human should check. You are not making a finding; your verdict is stored separately as an
agent verdict and never replaces the human's.

Answer with a decision, a type and a severity.

decision: "problem" (a research-integrity problem a reviewer should act on), "no problem", or "unsure".

type (each maps to REVIEWING.md's agent vocabulary):
- cross-author copying (agent_decided_dupe): different people, and one paper reproduces substantial
  text from the other that is each paper's own prose, not an accepted form of reuse.
- data copying (agent_decided_dupe): different people, and one paper presents another study's
  results, data, tables or figures (patient numbers, measurements, statistics, cohort descriptions)
  as its own. Use this rather than cross-author copying whenever the copied material includes the
  other study's data or results, even if prose is copied too.
- duplicate publication (REVIEWING.md's "a"): the same authors publish substantially the same paper
  twice (e.g. in two journals, or proceedings then a journal) without it being an acknowledged
  extended version.
- text recycling (REVIEWING.md's "a"): the same authors or group reuse parts of their own earlier
  text (methods, background) in a different paper, or a conference paper becomes an extended journal
  paper.
- same paper (REVIEWING.md's "p"): the two records are one work catalogued twice (preprint and
  published version, the same article under two DOIs).
- legitimate reuse: an accepted form of shared text: a co-published guideline or consensus statement,
  an authorised reprint, a templated report series (e.g. a government agency's standard template), a
  book chapter reprinting its author's article.
- shared third source: both papers quote or adapt the same external text (a standard protocol, a
  guideline, a legal text, a canonical passage by a named scholar) rather than each other.
- boilerplate (agent_decided_boilerplate): template/disclaimer/licence/masthead/funding/ethics text
  that many unrelated papers would contain.
- shared citations (agent_decided_citation): the shared text is formatted reference-list entries.
- artifact: not two different works' text: a PDF that isn't the paper its title names, pages printed
  together (front matter, adjacent abstracts), or one file filed under two records.
- unsure (agent_decided_unsure): the evidence doesn't support one type.

severity:
- high: data copying (another study's results or data presented as the copier's own); or
  cross-author copying of substantial original content (roughly a third of a paper or more), or
  of results/data, or with signs of fabrication (e.g. another study's results presented as the
  copier's own, a relabelled study setting).
- medium: cross-author copying of smaller parts; or duplicate publication of a substantially
  identical paper without disclosure.
- low: text recycling of methods/background by the same authors; small overlaps; copying that is
  cited.
- none: same paper, legitimate reuse, shared third source, boilerplate, shared citations, artifact.
Give the reason for the severity in severity_reason.

Checks REVIEWING.md requires, which your output must show you did:
1. Authorship from the PDFs, not the metadata. The metadata author lists come from a noisy index and
   are often wrong, truncated or empty. Read the byline in each paper's pdf_page1 text (the retrieved
   PDF's first page, via pdftotext) and list the names you find in byline_names_A / byline_names_B.
   Use pdf_page1, not the extracted opening text: text extraction can drop the title-and-byline block.
   Then check each paper's authors_metadata against its own page-1 byline. If the metadata names don't
   appear there, the metadata is wrong for that paper; say so in name_comparison and ignore it. Common
   shapes: a proceedings volume's editor lifted from a running header ("David C. Wyld et al. (Eds)"),
   a journal or publisher name, an affiliation, a truncated list. Compare authorship using the two
   page-1 bylines only. (REVIEWING.md non-optional step 4; a journal paper and its own proceedings
   version once passed as cross-author because one side's metadata "author" was the volume editor.)
2. Compare every name on one byline against every name on the other, including initials-only and
   differently punctuated or ordered forms ("Smith A." / "A. Smith" / "Smith, A.", "Ren Chuancheng" /
   "Chuancheng Ren"), and treating a multi-word surname ("Le Loher", "van der Berg", "De Souza") as one
   name. Put any match in shared_names and explain the comparison in name_comparison. If a
   byline can't be read from the page-1 text, say so and leave its list empty.
3. Each paper's title must match its own content: compare the title with its opening and middle
   text. A mismatch means a retrieval error (artifact), not copying, however strong the overlap.
4. For each shared passage, read context_before in both papers: a named-author lead-in ("X (2008)
   states/argues/summarizes ... as follows:") or quotation marks around the passage means both papers
   are quoting a third party. List those passage numbers in third_party_quotation_passages; they are
   weak evidence. Passages reporting a paper's own results are not quotations.
5. Passages flagged with a text_pattern match are known boilerplate or citation shapes; list
   boilerplate passages in boilerplate_passages, and if you see a recurring boilerplate shape that
   has no text_pattern yet, describe a distinctive phrase for it in possible_new_text_patterns.
6. later_cites_earlier: whether the later paper's reference list cites the earlier one (the packet's
   citation check helps). Copying a large block without attribution is still worth flagging when it is
   cited; a paraphrased sentence next to a citation is less clear-cut.
7. Years in the metadata can be wrong; say so if the text suggests otherwise.

Be conservative. cross-author copying or data copying requires that you read both bylines, found no shared name, both
titles match their content, and the shared text that remains after setting aside quotations and
boilerplate is substantial own prose. Base every reason on something in the packet, and name what a
human should check to confirm or overturn the verdict."""


LOCAL_ORDER = ["byline_names_A", "byline_names_B", "shared_names", "name_comparison", "title_matches_content_A",
               "title_matches_content_B", "later_cites_earlier", "third_party_quotation_passages",
               "boilerplate_passages", "reasons", "red_flags", "earlier_paper", "summary", "type", "decision",
               "severity", "severity_reason", "confidence", "checks_for_human", "possible_new_text_patterns"]
LOCAL_SCHEMA = {**SCHEMA, "properties": {k: SCHEMA["properties"][k] for k in LOCAL_ORDER}, "required": LOCAL_ORDER}


class LocalModel:
    """A llama.cpp llama-server (OpenAI-compatible /v1/chat/completions) standing in for the Anthropic API:
    same system prompt, same packet, the output held to the same JSON schema by the server's grammar
    (fields reordered, LOCAL_SCHEMA). Returns an object with the fields main() reads from an Anthropic
    response. Free, so cost is 0."""

    def __init__(self, url, effort, timeout=3600):
        import requests
        self.requests, self.url, self.effort, self.timeout = requests, url.rstrip("/"), effort, timeout
        models = self.requests.get(self.url + "/v1/models", timeout=30).json()
        self.model = "local:" + Path(models["data"][0]["id"]).name

    def create(self, req):
        from types import SimpleNamespace as NS
        body = {"messages": [{"role": "system", "content": SYSTEM}] + req["messages"],
                "max_tokens": 6000, "temperature": 0,
                "response_format": {"type": "json_schema", "json_schema": {"name": "verdict", "schema": LOCAL_SCHEMA}},
                # gpt-oss reads its reasoning effort from the chat template; other models ignore it
                "chat_template_kwargs": {"reasoning_effort": {"xhigh": "high", "max": "high"}.get(self.effort, self.effort)}}
        r = self.requests.post(self.url + "/v1/chat/completions", json=body, timeout=self.timeout)
        r.raise_for_status()
        d = r.json()
        ch = d["choices"][0]
        u = d.get("usage", {})
        return NS(usage=NS(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                           cache_creation_input_tokens=0, cache_read_input_tokens=0),
                  stop_reason={"stop": "end_turn", "length": "max_tokens"}.get(ch.get("finish_reason"), ch.get("finish_reason")),
                  stop_details=None, model=self.model, _request_id=d.get("id"),
                  content=[NS(type="text", text=ch["message"].get("content") or "")])


def client():
    if not os.environ.get("ANTHROPIC_API_KEY") and KEY_FILE.exists():
        return anthropic.Anthropic(api_key=KEY_FILE.read_text().strip())
    return anthropic.Anthropic()


AGENT_COLUMNS = ["decision", "type", "severity", "severity_reason", "reviewing_md_equivalent", "precondition_failures", "byline_names_a", "byline_names_b",
                 "shared_names", "name_comparison", "title_matches_content_a", "title_matches_content_b",
                 "later_cites_earlier", "third_party_quotation_passages", "boilerplate_passages",
                 "possible_new_text_patterns", "raw_verdict"]


def ensure_tables(db):
    db.executescript("""
    create table if not exists agent_reviews (
        agent_review_id integer primary key, pair_lo text, pair_hi text, doi_a text, doi_b text,
        prompt_version text, model text, effort text, verdict text, confidence text,
        earlier_paper text, summary text, reasons text, red_flags text, checks_for_human text,
        stop_reason text, input_tokens integer, output_tokens integer, cost_usd real,
        request_id text, created_at text,
        unique (pair_lo, pair_hi, prompt_version)
    );
    """)
    have = {r[1] for r in db.execute("pragma table_info(agent_reviews)")}
    for col in AGENT_COLUMNS:  # columns added after the table was first created (v2, v3)
        if col not in have:
            db.execute(f"alter table agent_reviews add column {col} text")
    db.executescript("""
    -- Pairs still awaiting a human (reviews has no row), with the agent's latest answer alongside.
    drop view if exists to_review_agent;
    create view to_review_agent as
    select t.*, a.decision as agent_decision, a.type as agent_type, a.severity as agent_severity,
           a.confidence as agent_confidence, a.earlier_paper as agent_earlier, a.summary as agent_summary,
           a.checks_for_human as agent_checks, a.prompt_version as agent_prompt_version
    from to_review t
    left join agent_reviews a on a.agent_review_id = (
        select agent_review_id from agent_reviews x
        where x.pair_lo = t.pair_lo and x.pair_hi = t.pair_hi and x.type is not null
        order by x.model like 'local:%', x.agent_review_id desc limit 1);

    -- The agent's answer in both views: a Claude review where there is one, else a local model's (the
    -- local models are a cheaper triage pass, measured against Claude on data/local_eval_pairs.tsv), the
    -- latest of either kind.
    -- One row per full-text pair: the measurements, the agent's and the human's decisions, and the
    -- effective type/severity (the human's where set, else the agent's; *_source says which).
    drop view if exists results;
    create view results as
    select f.pair_lo, f.pair_hi, f.doi_a, f.doi_b, f.year_a, f.year_b, f.ft_verdict, f.ft_pct_a, f.ft_pct_b,
           f.ft_matched_words, f.ft_longest, f.byline, f.page,
           a.decision as ai_decision, a.type as ai_type, a.severity as ai_severity, a.confidence as ai_confidence,
           a.summary as ai_summary, a.checks_for_human as ai_checks, a.prompt_version as ai_prompt_version,
           r.decision as human_decision, r.type as human_type, r.severity as human_severity, r.note as human_note,
           coalesce(r.type, a.type) as type,
           case when r.type is not null then 'human' when a.type is not null then 'ai' end as type_source,
           coalesce(r.severity, a.severity) as severity,
           case when r.severity is not null then 'human' when a.severity is not null then 'ai' end as severity_source
    from fulltext f
    left join reviews r on r.pair_lo = f.pair_lo and r.pair_hi = f.pair_hi
    left join agent_reviews a on a.agent_review_id = (
        select agent_review_id from agent_reviews x
        where x.pair_lo = f.pair_lo and x.pair_hi = f.pair_hi and x.type is not null
        order by x.model like 'local:%', x.agent_review_id desc limit 1);
    """)


def paper_meta(lib, pid):
    row = lib.execute("select title, year, doi from papers where id = ?", (pid,)).fetchone()
    authors = [n for (n,) in lib.execute(
        "select a.name from paper_authors pa join authors a on a.id = pa.author_id "
        "where pa.paper_id = ? order by pa.author_order", (pid,))]
    return {"title": row[0], "year": row[1], "doi": row[2], "authors_metadata": authors}


PDF_MIRRORS = []  # (prefix, replacement) from --pdf-mirror


def pdf_path(lib_path, lib, pid):
    """The PDF's path from the library, rewritten through --pdf-mirror (read a local copy of PDFs that live
    on the external drive, which drops off USB under load)."""
    row = lib.execute("select file_path from papers where id = ?", (pid,)).fetchone()
    if not row or not row[0]:
        return None
    path = str(Path(row[0]) if Path(row[0]).is_absolute() else Path(lib_path).parent / row[0])
    for old, new in PDF_MIRRORS:
        if path.startswith(old):
            path = new + path[len(old):]
    return Path(path)


def pdf_page1(lib_path, lib, pid, chars=PDF_PAGE1_CHARS):
    """First page of the retrieved PDF itself, as pdftotext reads it: REVIEWING.md wants bylines read
    from the PDF, not from the database's author list."""
    path = pdf_path(lib_path, lib, pid)
    if path is None or not path.exists():
        return None
    try:
        out = subprocess.run(["pdftotext", "-l", "1", "-layout", str(path), "-"], capture_output=True,
                             text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return " ".join(out.split())[:chars]


def citation_check(lib, cited_title, citing_pid):
    """Does the citing paper's reference list name the other paper (build_dupe_candidates.cites)?

    Extraction glues the running header/footer (which carries the paper's own title) onto reference
    entries, and in a copied pair the two titles are near-identical, so that header reads as a citation:
    2026-10-10, 21 of 66 pairs the check called cited were this, and the model had rejected every one. A
    header repeats on every page, so it lands on several entries; a real citation is one entry. Entries that
    match the citing paper's own title are dropped when there are 2+ of them. titles_near_identical tells
    the model the check can't separate the two cases on titles alone."""
    raws = [r for (r,) in lib.execute("select raw_text from citations where paper_id = ?", (citing_pid,))]
    own = (lib.execute("select title from papers where id = ?", (citing_pid,)).fetchone() or [""])[0] or ""
    own_hits = {i for i, r in enumerate(raws) if own and cites(own, [r])}
    header = own_hits if len(own_hits) >= 2 else set()
    hits = [r[:300] for i, r in enumerate(raws) if i not in header and cites(cited_title or "", [r])]
    return {"references_extracted": len(raws), "cites_other_by_title": bool(hits), "matching_references": hits[:3],
            "titles_near_identical": bool(own and cited_title and cites(cited_title, [own])),
            "entries_dropped_as_running_header": len(header)}


def packet(db, lib, f, lib_path=None, size="full"):
    z = PACKETS[size]
    a, b = f["paper_a"], f["paper_b"]
    words_a, words_b = ctp.load_paper_words(lib, a), ctp.load_paper_words(lib, b)
    runs = ctp.find_shingle_matches(words_a, words_b, shingle_size=10, x_drop=3)
    longest = sorted(runs, key=lambda r: -r.length)[:z["runs"]]
    opening = lambda ws: " ".join(w for w, _ in ws[:z["opening"]])
    lead = db.execute("select tier, band, abstracts, abs_run, retraction_watch, doi_a, pct_a, pct_b, shared "
                      "from lead_details where pair_lo = ? and pair_hi = ? order by primary_pair desc limit 1",
                      (f["pair_lo"], f["pair_hi"])).fetchone()
    gi = None
    if lead:
        # the lead's A/B can be the reverse of this pair's: key the index shares by DOI
        pa, pb = (lead["pct_a"], lead["pct_b"]) if (lead["doi_a"] or "").lower() == f["doi_a"].lower() \
            else (lead["pct_b"], lead["pct_a"])
        gi = {"tier": lead["tier"], "band": lead["band"], "abstracts": lead["abstracts"],
              "abstract_shared_run_words": lead["abs_run"], "retraction_watch": lead["retraction_watch"],
              "index_pct_of_A_sampled_5grams_in_B": pa, "index_pct_of_B_sampled_5grams_in_A": pb,
              "shared_sampled_5grams": lead["shared"]}
    ma, mb = paper_meta(lib, a), paper_meta(lib, b)
    middle = lambda ws: " ".join(w for w, _ in ws[len(ws) * 2 // 5: len(ws) * 2 // 5 + z["middle"]])
    before = lambda ws, i: " ".join(w for w, _ in ws[max(0, i - z["context"]):i])
    quoted = lambda t: any(q in t for q in ('"', "\u201c", "\u201d"))
    return {
        "paper_A": {**ma, "word_count": len(words_a), "pdf_page1": pdf_page1(lib_path, lib, a, z["page1"]),
                    "opening_text_extracted": opening(words_a), "middle_text_extracted": middle(words_a),
                    "citations_of_B": citation_check(lib, mb["title"], a)},
        "paper_B": {**mb, "word_count": len(words_b), "pdf_page1": pdf_page1(lib_path, lib, b, z["page1"]),
                    "opening_text_extracted": opening(words_b), "middle_text_extracted": middle(words_b),
                    "citations_of_A": citation_check(lib, ma["title"], b)},
        "wrong_pdf_check": f["wrong_pdf"] or "none (rank_paper_pairs.wrong_pdf_side found no title swap)",
        "full_text_overlap": {
            "method": "10-word exact word shingles, extended with x-drop 3",
            "pct_of_A_in_shared_runs": f["ft_pct_a"], "pct_of_B_in_shared_runs": f["ft_pct_b"],
            "matched_words": f["ft_matched_words"], "longest_run_words": f["ft_longest"],
            "number_of_runs": len(runs),
            "byline_check": f["byline"],
        },
        "general_index_evidence": gi,
        "longest_shared_passages": [
            {"passage": i, "words": r.length,
             "text_in_A": " ".join(r.text_a.split()[:z["run_words"]]),
             "text_in_B": " ".join(r.text_b.split()[:z["run_words"]]),
             "context_before_in_A": before(words_a, r.start_a), "context_before_in_B": before(words_b, r.start_b),
             "quotation_marks_in_passage_or_lead_in": quoted(before(words_a, r.start_a)[-80:] + r.text_a[:200]),
             "text_pattern": classify_dupes.classify_text(r.text_a),
             "position_in_A": f"word {r.start_a} of {len(words_a)}",
             "position_in_B": f"word {r.start_b} of {len(words_b)}"} for i, r in enumerate(longest, 1)],
    }


def request(args, pk):
    return dict(
        model=args.model,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content":
                   "Evidence packet for one pair (JSON). Give your triage verdict.\n\n" + json.dumps(pk, ensure_ascii=False)}],
        output_config={"effort": args.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DATA / "gi.sqlite3"))
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--prompt-version", default="v4", help="v4 = v3 + metadata authors checked against the page-1 byline (REVIEWING.md step 4); "
                    "v3 = decision/type/severity; v2 = the REVIEWING.md checks (bylines from PDFs, "
                    "name comparison, title/content, third-party quotations, boilerplate patterns, citations)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4, help="API calls in flight at once")
    ap.add_argument("--runs", nargs="+", help="only pairs from these fulltext runs")
    ap.add_argument("--pair-list", help="only these pairs: a file of pair_lo<TAB>pair_hi lines")
    ap.add_argument("--pdf-mirror", action="append", default=[], metavar="OLD=NEW",
                    help="read PDFs whose path starts with OLD from NEW instead (repeatable)")
    ap.add_argument("--max-cost", type=float, default=10.0, help="USD; stop before the next call past this")
    ap.add_argument("--backend", choices=["anthropic", "local"], default="anthropic",
                    help="local = a llama.cpp llama-server at --local-url (see run_local_model.sh); free")
    ap.add_argument("--local-url", default="http://127.0.0.1:8080")
    ap.add_argument("--packet", choices=list(PACKETS), default="full",
                    help="evidence packet size: full (default) or slim (a local model on the CPU; ~40%% fewer tokens)")
    ap.add_argument("--dry-run", action="store_true", help="build packets and count tokens only (no billable call)")
    ap.add_argument("--show-packet", action="store_true", help="print each evidence packet")
    ap.add_argument("--include-reviewed", action="store_true", help="also pairs a human has already reviewed")
    ap.add_argument("--redo", action="store_true", help="re-ask pairs this prompt version already has")
    args = ap.parse_args()

    PDF_MIRRORS.extend(tuple(m.split("=", 1)) for m in args.pdf_mirror)
    local = None
    if args.backend == "local":
        local = LocalModel(args.local_url, args.effort)
        # its own prompt_version, so local verdicts never overwrite the API's rows for the same pair
        if "local:" not in args.prompt_version:
            args.prompt_version = f"{args.prompt_version}+{local.model}"
        args.model = local.model
        print(f"local model {local.model} at {args.local_url}; rows stored as prompt version {args.prompt_version}")
    db = sqlite3.connect(args.db, timeout=300)
    db.row_factory = sqlite3.Row
    ensure_tables(db)
    where = "f.ft_verdict = 'real overlap'"
    if not args.include_reviewed:
        where += " and not exists (select 1 from reviews r where r.pair_lo = f.pair_lo and r.pair_hi = f.pair_hi)"
    if not args.redo:
        where += (" and not exists (select 1 from agent_reviews x where x.pair_lo = f.pair_lo and "
                  f"x.pair_hi = f.pair_hi and x.prompt_version = ? and x.verdict is not null)")
    params = [] if args.redo else [args.prompt_version]
    if args.runs:
        where += f" and f.run in ({','.join('?' * len(args.runs))})"
        params += args.runs
    if args.pair_list:
        want = [tuple(l.split("\t")[:2]) for l in Path(args.pair_list).read_text().splitlines() if "\t" in l]
        where += " and (f.pair_lo, f.pair_hi) in (" + ",".join("(?, ?)" for _ in want) + ")"
        params += [x for p in want for x in p]
    pairs = db.execute(f"select f.* from fulltext f where {where} order by max(f.ft_pct_a, f.ft_pct_b) desc",
                       params).fetchall()
    if args.limit:
        pairs = pairs[:args.limit]
    print(f"{len(pairs)} pair(s) to review (prompt {args.prompt_version}, {args.model}, effort {args.effort})")

    try:
        api = client()
    except anthropic.AnthropicError:
        if not args.dry_run and not local:
            raise SystemExit("no Anthropic credentials: set ANTHROPIC_API_KEY, run `ant auth login`, or put a key in "
                             f"{KEY_FILE} (mode 600)")
        api = anthropic.Anthropic(api_key="dry-run-no-key")
    price_in, price_out = (0.0, 0.0) if local else PRICES.get(args.model, (4.00, 20.00))
    spent, done, skipped_missing = 0.0, 0, 0
    libs = {}
    # --workers API calls in flight at once; packets are built and results written here, in the main
    # thread (sqlite connections stay single-threaded). --max-cost is checked before each new call, so a
    # run can end up to workers - 1 calls past it.
    pool = ThreadPoolExecutor(max(1, args.workers))
    inflight = []

    def call(req):
        if local:
            try:
                return local.create(req)
            except Exception as e:  # a server error or timeout: report it like an API error, don't stop the run
                return RuntimeError(f"local model: {e}")
        for _ in range(5):
            try:
                return api.beta.messages.create(**req, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except anthropic.RateLimitError:
                print("  rate limited; waiting 60s", flush=True)
                time.sleep(60)
            except anthropic.APIStatusError as e:
                return e
        return None

    def drain_one():
        f, fut = inflight.pop(0)
        resp = fut.result()
        if resp is None:
            print(f"  still rate limited after 5 tries, skipped: {f['doi_a']} | {f['doi_b']}")
        elif isinstance(resp, anthropic.APIStatusError):
            print(f"  API error {resp.status_code} for {f['doi_a']} | {f['doi_b']}: {resp.message}")
        elif isinstance(resp, RuntimeError):
            print(f"  {resp} for {f['doi_a']} | {f['doi_b']}")
        else:
            finish(f, resp)

    def finish(f, resp):
        nonlocal spent, done
        u = resp.usage
        cost = (u.input_tokens + (u.cache_creation_input_tokens or 0) * 1.25 + (u.cache_read_input_tokens or 0) * 0.1) \
            * price_in / 1e6 + u.output_tokens * price_out / 1e6
        spent += cost
        v = {}
        if resp.stop_reason == "refusal":
            print(f"  refused: {f['doi_a']} | {f['doi_b']} ({resp.stop_details.category if resp.stop_details else ''})")
        else:
            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                v = json.loads(text)
            except json.JSONDecodeError:
                print(f"  unparseable output for {f['doi_a']} | {f['doi_b']} (stop_reason {resp.stop_reason})")
        # REVIEWING.md's non-optional steps before an agent calls a dupe: enforced here, not just asked
        # for. A likely_copying verdict that fails one is stored as unsure with the failure named.
        raw_verdict, failures = v.get("type"), []
        if raw_verdict in ("cross-author copying", "data copying"):
            if not v.get("byline_names_A") or not v.get("byline_names_B"):
                failures.append("bylines not read from both PDFs")
            if v.get("shared_names"):
                failures.append("a shared byline name: " + ", ".join(v["shared_names"]))
            if "no" in (v.get("title_matches_content_A"), v.get("title_matches_content_B")):
                failures.append("a title does not match its own content")
            if failures:
                v.update(type="unsure", decision="unsure")
        if v.get("type") not in (None, *TYPES):
            failures.append(f"type outside the schema: {v.get('type')}")
            v.update(type="unsure", decision="unsure")
        # consistency: a non-problem type is never a problem and has no severity; unsure is unsure
        if v.get("type") and v["type"] not in PROBLEM_TYPES:
            if v["type"] == "unsure":
                v["decision"] = "unsure"
            else:
                v.update(decision="no problem", severity="none")
        v["verdict"] = v.get("type")
        db.execute("""insert into agent_reviews (pair_lo, pair_hi, doi_a, doi_b, prompt_version, model, effort,
                      verdict, confidence, earlier_paper, summary, reasons, red_flags, checks_for_human, stop_reason,
                      input_tokens, output_tokens, cost_usd, request_id, created_at,
                      reviewing_md_equivalent, precondition_failures, byline_names_a, byline_names_b, shared_names,
                      name_comparison, title_matches_content_a, title_matches_content_b, later_cites_earlier,
                      third_party_quotation_passages, boilerplate_passages, possible_new_text_patterns, raw_verdict,
                      decision, type, severity, severity_reason)
                      values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      on conflict (pair_lo, pair_hi, prompt_version) do update set
                      model=excluded.model, effort=excluded.effort, verdict=excluded.verdict,
                      confidence=excluded.confidence, earlier_paper=excluded.earlier_paper,
                      summary=excluded.summary, reasons=excluded.reasons, red_flags=excluded.red_flags,
                      checks_for_human=excluded.checks_for_human, stop_reason=excluded.stop_reason,
                      input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                      cost_usd=excluded.cost_usd, request_id=excluded.request_id, created_at=excluded.created_at,
                      reviewing_md_equivalent=excluded.reviewing_md_equivalent,
                      precondition_failures=excluded.precondition_failures, byline_names_a=excluded.byline_names_a,
                      byline_names_b=excluded.byline_names_b, shared_names=excluded.shared_names,
                      name_comparison=excluded.name_comparison,
                      title_matches_content_a=excluded.title_matches_content_a,
                      title_matches_content_b=excluded.title_matches_content_b,
                      later_cites_earlier=excluded.later_cites_earlier,
                      third_party_quotation_passages=excluded.third_party_quotation_passages,
                      boilerplate_passages=excluded.boilerplate_passages,
                      possible_new_text_patterns=excluded.possible_new_text_patterns, raw_verdict=excluded.raw_verdict,
                      decision=excluded.decision, type=excluded.type, severity=excluded.severity,
                      severity_reason=excluded.severity_reason""",
                   (f["pair_lo"], f["pair_hi"], f["doi_a"], f["doi_b"], args.prompt_version, resp.model, args.effort,
                    v.get("verdict"), v.get("confidence"), v.get("earlier_paper"), v.get("summary"),
                    json.dumps(v.get("reasons", [])), json.dumps(v.get("red_flags", [])),
                    json.dumps(v.get("checks_for_human", [])), resp.stop_reason, u.input_tokens, u.output_tokens,
                    round(cost, 5), resp._request_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    REVIEWING_MD.get(v.get("verdict")), json.dumps(failures), json.dumps(v.get("byline_names_A", [])),
                    json.dumps(v.get("byline_names_B", [])), json.dumps(v.get("shared_names", [])),
                    v.get("name_comparison"), v.get("title_matches_content_A"), v.get("title_matches_content_B"),
                    v.get("later_cites_earlier"), json.dumps(v.get("third_party_quotation_passages", [])),
                    json.dumps(v.get("boilerplate_passages", [])), json.dumps(v.get("possible_new_text_patterns", [])),
                    raw_verdict, v.get("decision"), v.get("type"), v.get("severity"), v.get("severity_reason")))
        db.commit()
        done += 1
        print(f"  [{done}] {v.get('decision', '-'):<10} {v.get('type', '-'):<22} {v.get('severity', ''):<6} ${cost:.3f}  "
              f"{f['doi_a']} | {f['doi_b']}")

    for f in pairs:
        if spent >= args.max_cost:
            print(f"stopping: spent ${spent:.2f} >= --max-cost ${args.max_cost:.2f}")
            break
        lib_path = f["library"] if Path(f["library"]).is_absolute() else str(DATA / f["library"])
        lib = libs.setdefault(lib_path, sqlite3.connect(lib_path))
        # REVIEWING.md's bylines come from the PDFs: a pair whose PDF file isn't there (the external drive
        # dropped off USB, 2026-10-09) is skipped, not reviewed from the extracted text alone
        missing = [p for p in (pdf_path(lib_path, lib, f["paper_a"]), pdf_path(lib_path, lib, f["paper_b"]))
                   if p is None or not p.exists()]
        if missing:
            skipped_missing += 1
            print(f"  skipped, PDF not found ({missing[0]}): {f['doi_a']} | {f['doi_b']}")
            continue
        pk = packet(db, lib, f, lib_path, args.packet)
        req = request(args, pk)
        if args.show_packet:
            print(json.dumps(pk, indent=1, ensure_ascii=False))
        if args.dry_run:
            try:
                n, how = api.messages.count_tokens(model=req["model"], system=req["system"],
                                                   messages=req["messages"]).input_tokens, "counted"
            except (anthropic.AuthenticationError, TypeError):
                # no credentials: a rough estimate (about 3.5 characters per token for this text)
                n, how = int((len(SYSTEM) + len(req["messages"][0]["content"])) / 3.5), "estimated"
            spent += n * price_in / 1e6
            print(f"  {f['doi_a']} | {f['doi_b']}: {n:,} input tokens {how} (~${n * price_in / 1e6:.3f} input)")
            continue
        while len(inflight) >= args.workers:
            drain_one()
        inflight.append((f, pool.submit(call, req)))
    while inflight:
        drain_one()
    pool.shutdown()
    if args.dry_run:
        print(f"dry run: about ${spent:.2f} of input for {len(pairs)} pair(s), plus output "
              f"(thinking + verdict, typically 1-3k tokens = ${price_out * 2e3 / 1e6:.2f} per pair)")
    else:
        print(f"done: {done} reviewed, ${spent:.2f} spent"
              + (f"; {skipped_missing} skipped because a PDF wasn't found" if skipped_missing else ""))


if __name__ == "__main__":
    main()
