#!/usr/bin/env python3
"""Interactive CLI for reviewing candidates in library.sqlite3's potential_dupes table.

This is the first of the two UXs todo.md's "UX" section asks for: "A cli that easily
lets me see a potential dupes and lets me flag it as a duplicate or not... look like
git diff... confirmation to be dupe(d) for confirmed, (f)alse positive (u) for
unsure." (The second -- a webpage -- isn't built yet.)

No pipeline stage needs to have finished for this to be useful: it just reads whatever
build_dupe_candidates.py has already persisted, so it's safe to run mid-embedding, same
as everything else in this project.

Diff rendering is a word-level diff (difflib.SequenceMatcher over whitespace-preserving
tokens): each paragraph's full text is printed under its own file/¶ header (a/earlier
first, then b/later), with the words that differ from the other side highlighted --
red in a, green in b -- rather than merged into one interleaved line. [-...-]/{+...+}
markers are the fallback when color is off (piped output, --no-color, or not a tty),
same markers git's own plain word-diff mode uses. Pairs are shown a=earlier, b=later
when build_dupe_candidates.py resolved chronology (earlier_paper_id/later_paper_id);
otherwise in paragraph_id_1/2 order, since which one's "original" is genuinely unknown.

Review is per-pair and immediate: each decision commits right away (not batched), so
quitting (q) at any point loses nothing -- whatever wasn't reached is simply still
'unreviewed' for next time. Filters (--author, --paper, --same-author-only,
--different-author-only, --cited-only, etc.) cover todo.md's "let me look at the potential matches by author"
and "make it easy to go through all the dupes in a single paper one by one"
(--order paper) asks.

(b)oilerplate is a sixth action, distinct from (f)alse-positive: reused boilerplate
text (publisher disclaimers, standard methodology templates, etc. -- the same kind of
thing classify_dupes.py's fixed pattern library screens for automatically) tends to
hash into the same LSH bucket across dozens or hundreds of unrelated papers (see
lsh_index.py's own note on skewed bucket occupancy), so one candidate pair being
boilerplate is strong evidence every other candidate built from that same bucket is
boilerplate too. Pressing (b) marks the current pair *and* every other potential_dupes
row whose both paragraphs fall in the same LSH bucket cluster as 'boilerplate' in one
shot, instead of making the reviewer click through each one individually
(mark_boilerplate()). Falls back to marking just the current pair if the two
paragraphs don't actually share an LSH bucket (e.g. candidates built with
build_dupe_candidates.py --brute-force, which doesn't use the LSH index at all).

(c)itation is the same bucket-cluster cascade as (b)oilerplate (mark_bucket_status(),
shared by both), just for a different pattern: two papers independently citing the
same source often render it as near-identical formatted reference text (same
author-year-title-venue string), which reads as a textual duplicate but isn't one.
Pressing (c) marks the current pair and every other candidate sharing its LSH bucket
cluster as 'citation' in one shot, same semantics as (b) otherwise (see
mark_bucket_status()'s docstring).

(p)apers-are-the-same is a different kind of false positive: paper_id_1 and
paper_id_2 are two separate `papers` rows (e.g. retrieved twice from different
URLs/sources) that a reviewer recognizes -- from the title+year shown in each
candidate's header -- are actually the same underlying paper. Every candidate between
that pair is then not a real cross-paper match at all, so (p) corrects same_paper to 1
(and nulls the now-vacuous same_author/earlier_paper_id/later_paper_id/
later_cites_earlier fields, matching build_dupe_candidates.py's own convention for
genuine same-paper pairs) on every potential_dupes row between those two papers in one
shot (mark_same_paper()). Deliberately leaves status/reviewed_at alone -- this
corrects a paper-identity mistake, not a verdict on the text -- so a future
--cross-paper-only session won't show these again, but a plain rerun still can.

The screen clears between candidates by default (ANSI escape codes, tty only -- never
when output is piped/redirected) so each new candidate starts on a blank screen
instead of scrolling past the last one; --no-clear turns this off.
"""

import argparse
import difflib
import re
import sqlite3
import sys
import time
from pathlib import Path

import db

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

WORD_RE = re.compile(r"\S+\s*|\s+")
# Trailing whitespace rides along with the word before it (not its own token) --
# tokenizing whitespace separately (r"\S+|\s+") let a shared space between two
# DIFFERENT adjacent words match as "equal" in the SequenceMatcher, splitting one
# real change ("machine learning" -> "ML systems") into two disjoint per-word
# colored patches with an uncolored gap between them instead of one continuous
# highlighted span. Attaching the space to its word means two adjacent replaced
# words no longer have an "equal" token between them, so get_opcodes() merges them
# into a single replace run, matching how git's own word-diff highlights.

ACTIONS = {"d": "confirmed", "f": "false_positive", "u": "unsure"}
# "b" (boilerplate) isn't in ACTIONS -- it doesn't just set this row's status, it
# cascades to every other candidate sharing this pair's LSH bucket (see
# mark_boilerplate()), so it's handled separately in the main loop.

# Agent-authored verdicts get their own status vocabulary, one per human ACTIONS/
# mark_boilerplate/mark_citation status value ("agent_decided_" + the human name, except
# "confirmed" -> "agent_decided_dupe" to read unambiguously on its own), rather than
# writing directly into "confirmed"/"boilerplate"/etc. This is deliberate, not cosmetic:
# an agent's verdict is not the same trust level as a human's, and collapsing them into
# the same status would make it impossible to tell, later, which candidates still need a
# human look and which were already reviewed by a person. See REVIEWING.md's "agent
# review" section. "p" (papers-are-the-same) has no agent variant -- mark_same_paper()
# never sets status/reviewed_at for humans either (see its own docstring), so there's
# nothing to distinguish; an agent applying "p" uses the exact same function, unchanged.
AGENT_STATUS_PREFIX = "agent_decided_"
AGENT_ACTIONS = {
    "d": "agent_decided_dupe",
    "f": "agent_decided_false_positive",
    "b": "agent_decided_boilerplate",
    "c": "agent_decided_citation",
    "u": "agent_decided_unsure",
}
AGENT_DECIDED_STATUSES = sorted(set(AGENT_ACTIONS.values()))


# Top ~100 English function/stop words -- a rough but dependency-free "is this English"
# signal (no language-detection library is installed, and this project keeps its
# dependency list deliberately small -- see requirements.txt). A real English paragraph
# typically scores well above 0.1 on english_score() below; a paragraph in another
# language (even one sharing the Latin alphabet, like French/German/Spanish) scores at
# or near 0.0, since its own function words essentially never collide with English ones.
COMMON_ENGLISH_WORDS = frozenset("""
the of and to a in is it you that he was for on are with as i his they be
at one have this from or had by word but not what all were we when your
can said there use an each which she do how their if will up other about
out many then them these so some her would make like him into time has
look two more write go see number no way could people my than first water
been call who its now find long down day did get come made may part
""".split())


ANY_ALPHA_RE = re.compile(r"[^\W\d_]", re.UNICODE)  # one alphabetic char, any script
LATIN_ALPHA_RE = re.compile(r"[A-Za-z]")

# Below this fraction of Latin-alphabet characters (out of all alphabetic characters,
# any script), the text is treated as not-English outright rather than trusting the
# word-overlap ratio below -- see the 2026-08-30 bug note on english_score() for why.
MIN_LATIN_ALPHA_FRACTION = 0.5


def english_score(text):
    """Fraction of `text`'s alphabetic word-tokens that are common English
    words -- see COMMON_ENGLISH_WORDS. Text in another language, even one
    using the Latin alphabet, still scores near 0 (its words just aren't in
    the English list). 0.0 for empty/whitespace-only text rather than
    dividing by zero.

    Bug found 2026-08-30 (anthropology field-corpus review) and fixed here:
    the denominator used to be "how many Latin-alphabet word-tokens did we
    find", on the assumption a non-Latin script (Cyrillic, Greek, ...) finds
    none at all and scores exactly 0. That's only true when literally zero
    Latin tokens appear -- a nearly-all-Cyrillic bibliography entry like
    '...Вконтакте // Историческая этнология. 2016. Т. 1, No 2. С. 276-292.'
    has exactly one Latin token ("No", from the Russian "Number" abbreviation,
    which also happens to be a common English word) and scored a perfect 1.0,
    the opposite of what the filter is for. Fixed by first checking what
    fraction of ALL alphabetic characters (any script) are Latin at all --
    well below half here -- and only trusting the original Latin-word-overlap
    ratio once that gate passes. A real English (or other Latin-script
    language's) paragraph is unaffected: its alphabetic characters are
    already ~100% Latin, so it clears the gate the same as before this fix."""
    if not text:
        return 0.0
    lowered = text.lower()
    total_alpha = len(ANY_ALPHA_RE.findall(lowered))
    if total_alpha == 0:
        return 0.0
    if len(LATIN_ALPHA_RE.findall(lowered)) / total_alpha < MIN_LATIN_ALPHA_FRACTION:
        return 0.0
    words = re.findall(r"[A-Za-z']+", lowered)
    if not words:
        return 0.0
    return sum(1 for w in words if w in COMMON_ENGLISH_WORDS) / len(words)


def title_similarity(title1, title2):
    """0..1 title-string similarity -- same technique (and, by default, the
    same acceptance threshold, 0.82) retrieve_papers.py uses to accept a
    Crossref bibliographic title match, reused here so `--min-title-similarity`/
    `--max-title-similarity` mean the same thing a reviewer would already
    associate with "these titles are basically the same". NULL titles
    (registered as a SQL function -- see main()) compare as dissimilar rather
    than erroring."""
    if not title1 or not title2:
        return 0.0
    return difflib.SequenceMatcher(None, title1.lower(), title2.lower()).ratio()


def load_candidates(conn, status, author, paper, min_similarity, cross_paper_only,
                     same_author_only, different_author_only, cited_only, uncited_only,
                     min_lcs_ratio, min_ngram_jaccard, require_overlap_checked,
                     min_title_similarity, max_title_similarity, min_english_score, order):
    query = """
        SELECT pd.id, pd.paragraph_id_1, pd.paragraph_id_2, pd.paper_id_1, pd.paper_id_2,
               pd.similarity, pd.same_paper, pd.same_author, pd.later_cites_earlier,
               pd.earlier_paper_id, pd.later_paper_id, pd.lcs_ratio, pd.ngram_jaccard, pd.status,
               pr1.para_index AS idx1, pr1.text AS text1,
               pr2.para_index AS idx2, pr2.text AS text2,
               p1.title AS title1, p1.file_path AS path1, p1.year AS year1,
               p2.title AS title2, p2.file_path AS path2, p2.year AS year2
        FROM potential_dupes pd
        JOIN paragraphs pr1 ON pr1.id = pd.paragraph_id_1
        JOIN paragraphs pr2 ON pr2.id = pd.paragraph_id_2
        JOIN papers p1 ON p1.id = pd.paper_id_1
        JOIN papers p2 ON p2.id = pd.paper_id_2
        WHERE 1=1

    """
    params = []
    if status:
        # Comma-separated list ("boilerplate,citation") -- most callers pass a single
        # status, so keep that case a plain "=" rather than a 1-element IN (...).
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if len(statuses) == 1:
            query += " AND pd.status = ?"
            params.append(statuses[0])
        else:
            query += f" AND pd.status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
    if min_similarity is not None:
        query += " AND pd.similarity >= ?"
        params.append(min_similarity)
    if cross_paper_only:
        query += " AND pd.same_paper = 0"
    if same_author_only:
        query += " AND pd.same_author = 1"
    if different_author_only:
        # same_author is NULL (not 0) for same-paper pairs, where "different author"
        # would be vacuously false -- comparing to 0 rather than != 1 excludes those
        # the same way uncited_only excludes later_cites_earlier IS NULL below.
        query += " AND pd.same_author = 0"
    if cited_only:
        query += " AND pd.later_cites_earlier = 1"
    if uncited_only:
        query += " AND pd.later_cites_earlier = 0"
    # lcs_ratio/ngram_jaccard are NULL until build_dupe_candidates.py has run since this
    # feature was added (see text_overlap.py) -- a bare "> threshold" comparison against
    # NULL is neither true nor false in SQL and just silently drops the row, which is the
    # right default (don't hide not-yet-checked candidates from --min-similarity-only
    # browsing), but --require-overlap-checked makes that explicit for anyone who wants to
    # be sure they're only seeing candidates the more accurate check has actually run on.
    if min_lcs_ratio is not None:
        query += " AND pd.lcs_ratio >= ?"
        params.append(min_lcs_ratio)
    if min_ngram_jaccard is not None:
        query += " AND pd.ngram_jaccard >= ?"
        params.append(min_ngram_jaccard)
    if require_overlap_checked:
        query += " AND pd.lcs_ratio IS NOT NULL"
    # title_similarity is a Python function registered via conn.create_function (see
    # main()) -- SQLite has no builtin string-similarity, and computing it in SQL
    # per-row via a UDF is simpler than fetching everything unfiltered and
    # re-filtering in Python, and still composes with every other filter.
    if min_title_similarity is not None:
        query += " AND title_similarity(p1.title, p2.title) >= ?"
        params.append(min_title_similarity)
    if max_title_similarity is not None:
        # The inverse of --min-title-similarity: filter OUT the near-identical-title
        # ("these are really the same paper") case instead of isolating it, for a
        # review pass that only wants genuinely different papers.
        query += " AND title_similarity(p1.title, p2.title) <= ?"
        params.append(max_title_similarity)
    if min_english_score is not None:
        # english_score is a Python UDF too (see main()) -- require BOTH paragraphs to
        # clear the bar, since a real duplicate pair is normally either both English or
        # both some other language together (the whole source paper is in that
        # language), not a mix.
        query += " AND english_score(pr1.text) >= ? AND english_score(pr2.text) >= ?"
        params.extend([min_english_score, min_english_score])
    if paper:
        query += " AND (p1.title LIKE ? OR p2.title LIKE ?)"
        params.extend([f"%{paper}%", f"%{paper}%"])
    if author:
        query += """ AND pd.id IN (
            SELECT pda.potential_dupe_id FROM potential_dupe_authors pda
            JOIN authors a ON a.id = pda.author_id
            WHERE a.name LIKE ?
        )"""
        params.append(f"%{author}%")

    query += {
        "similarity": " ORDER BY pd.similarity DESC",
        "paper": " ORDER BY pd.paper_id_1, pd.paper_id_2, pd.similarity DESC",
    }[order]

    return conn.execute(query, params).fetchall()


def load_candidate_by_id(conn, candidate_id):
    """Same column shape as load_candidates() (so the result is a drop-in `row` for
    print_candidate()/mark_boilerplate()/mark_citation()/mark_same_paper()), for exactly
    one potential_dupes row by id -- what --agent-verdict operates on. Returns None if the
    id doesn't exist (already deleted as orphaned, or never existed)."""
    return conn.execute(
        """
        SELECT pd.id, pd.paragraph_id_1, pd.paragraph_id_2, pd.paper_id_1, pd.paper_id_2,
               pd.similarity, pd.same_paper, pd.same_author, pd.later_cites_earlier,
               pd.earlier_paper_id, pd.later_paper_id, pd.lcs_ratio, pd.ngram_jaccard, pd.status,
               pr1.para_index AS idx1, pr1.text AS text1,
               pr2.para_index AS idx2, pr2.text AS text2,
               p1.title AS title1, p1.file_path AS path1, p1.year AS year1,
               p2.title AS title2, p2.file_path AS path2, p2.year AS year2
        FROM potential_dupes pd
        JOIN paragraphs pr1 ON pr1.id = pd.paragraph_id_1
        JOIN paragraphs pr2 ON pr2.id = pd.paragraph_id_2
        JOIN papers p1 ON p1.id = pd.paper_id_1
        JOIN papers p2 ON p2.id = pd.paper_id_2
        WHERE pd.id = ?
        """,
        (candidate_id,),
    ).fetchone()


def apply_agent_verdict(conn, row, key):
    """Applies one verdict the same way a human's keypress would in the main loop, but
    writing the agent_decided_* status vocabulary (AGENT_ACTIONS) instead of the human one
    -- see AGENT_ACTIONS' comment for why they're kept distinct. Reuses mark_boilerplate/
    mark_citation/mark_same_paper unchanged (mark_bucket_status() already takes the status
    string as a parameter, so the bucket-cascade logic doesn't need duplicating for the
    agent path). Returns the set of potential_dupes ids touched, same contract as the
    mark_* functions, or {row['id']} for a plain d/f/u status write."""
    if key not in ("d", "f", "b", "c", "p", "u"):
        raise ValueError(f"unrecognized verdict key {key!r} -- must be one of d/f/b/c/p/u")
    if key == "b":
        return mark_bucket_status(conn, row, AGENT_ACTIONS["b"])
    if key == "c":
        return mark_bucket_status(conn, row, AGENT_ACTIONS["c"])
    if key == "p":
        return mark_same_paper(conn, row)  # no agent variant -- see AGENT_ACTIONS' comment
    conn.execute(
        "UPDATE potential_dupes SET status = ?, reviewed_at = ? WHERE id = ?",
        (AGENT_ACTIONS[key], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row["id"]),
    )
    conn.commit()
    return {row["id"]}


def bucket_cluster(conn, paragraph_id_1, paragraph_id_2):
    """Every paragraph_id sharing an LSH bucket (table_num, bucket_key) with
    BOTH paragraphs of this candidate pair -- i.e. the buckets that actually
    connected this pair as an LSH candidate in the first place (see
    lsh_index.py), expanded to their full membership. Empty if the two
    paragraphs don't share a bucket at all (e.g. this candidate came from
    build_dupe_candidates.py --brute-force, which never touches lsh_buckets)."""
    shared_buckets = conn.execute(
        """
        SELECT table_num, bucket_key FROM lsh_buckets
        WHERE paragraph_id IN (?, ?)
        GROUP BY table_num, bucket_key
        HAVING COUNT(DISTINCT paragraph_id) = 2
        """,
        (paragraph_id_1, paragraph_id_2),
    ).fetchall()
    members = set()
    for table_num, bucket_key in shared_buckets:
        rows = conn.execute(
            "SELECT paragraph_id FROM lsh_buckets WHERE table_num = ? AND bucket_key = ?",
            (table_num, bucket_key),
        ).fetchall()
        members.update(r[0] for r in rows)
    return members


def mark_bucket_status(conn, row, status_value):
    """Shared sweep behind both (b)oilerplate and (c)itation: mark this
    candidate, and every other potential_dupes row built entirely from
    paragraphs in the same LSH bucket cluster, with `status_value` -- one
    press covers the whole cluster instead of the reviewer clicking through
    each repeat of the same reused text individually. Overwrites whatever
    status a swept-up row already had (including a prior human verdict):
    recognizing a cluster as boilerplate/a shared citation is new information
    that should win, not be silently skipped because something in the
    cluster was reviewed before the pattern was noticed. Returns the set of
    potential_dupes ids updated (including this row's own), so the caller
    can skip re-presenting any of them that haven't been shown yet this
    session."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    members = bucket_cluster(conn, row["paragraph_id_1"], row["paragraph_id_2"])
    if not members:
        conn.execute(
            "UPDATE potential_dupes SET status = ?, reviewed_at = ? WHERE id = ?",
            (status_value, now, row["id"]),
        )
        conn.commit()
        print(f"  no shared LSH bucket for this pair (brute-force candidate?) -- "
              f"marked only this one as {status_value}")
        return {row["id"]}

    # Deliberately NOT "WHERE paragraph_id_1 IN (...) AND paragraph_id_2 IN (...)" with
    # `members` spliced in as placeholders: real boilerplate/citation text hashes into
    # exactly the huge buckets this feature targets (measured up to ~52,000 paragraphs in
    # one bucket on this project's own corpus), and SQLite's planner turns "col IN (huge
    # list) AND col2 IN (huge list)" into a 100+ second query against even the ~8.5k-row
    # potential_dupes table -- confirmed directly, that's the reported timeout.
    # potential_dupes itself is always small (it's the post-threshold candidate set, not
    # the corpus), so one unfiltered scan of it plus an in-memory set-membership check is
    # both simpler and avoids the pathological query entirely, regardless of bucket size.
    ids = [
        pd_id for pd_id, p1, p2 in conn.execute(
            "SELECT id, paragraph_id_1, paragraph_id_2 FROM potential_dupes"
        )
        if p1 in members and p2 in members
    ]
    conn.executemany(
        "UPDATE potential_dupes SET status = ?, reviewed_at = ? WHERE id = ?",
        [(status_value, now, i) for i in ids],
    )
    conn.commit()
    print(f"  marked {len(ids)} candidate(s) as {status_value} "
          f"({len(members)} paragraph(s) sharing this LSH bucket cluster)")
    return set(ids)


def mark_boilerplate(conn, row):
    return mark_bucket_status(conn, row, "boilerplate")


def mark_citation(conn, row):
    """A shared citation/reference-list entry -- two papers independently
    citing the same source often render it with near-identical formatted
    text (same author-year-title-venue string), which reads as a textual
    duplicate but isn't one; same cascade as mark_boilerplate() (see
    mark_bucket_status()), just a different terminal status."""
    return mark_bucket_status(conn, row, "citation")


def mark_same_paper(conn, row):
    """Handles the case where paper_id_1 and paper_id_2 are two separate
    `papers` rows that are actually the same underlying paper (e.g. retrieved
    twice from different sources/URLs) -- title+year matching is what tips a
    reviewer off, per review_dupes's own display. Every potential_dupes
    candidate between that pair of papers is then not a real cross-paper
    duplicate finding at all (it's just the same text matching itself under
    two paper_ids), so this corrects same_paper to 1 for every one of them in
    one shot, rather than the reviewer re-diagnosing the same paper mixup on
    every remaining candidate between the two.

    Also nulls out same_author/earlier_paper_id/later_paper_id/
    later_cites_earlier on the corrected rows -- build_dupe_candidates.py's
    own convention for genuine same_paper=1 pairs is NULL rather than a
    vacuously-true/guessed value (see its docstring), so a same_paper
    correction here should leave those fields in the same state a same-paper
    candidate would have had from the start.

    Deliberately does NOT touch status/reviewed_at: this is a data
    correction (same_paper was computed wrong because two `papers` rows
    shouldn't have been separate in the first place), not a human verdict on
    the paragraph text itself. Returns the set of potential_dupes ids
    corrected (including this row's own), so the caller can skip
    re-presenting any of them for the rest of this session."""
    paper_id_1, paper_id_2 = row["paper_id_1"], row["paper_id_2"]
    if paper_id_1 == paper_id_2:
        print("  paper_id_1 == paper_id_2 already -- this candidate is already same_paper")
        return {row["id"]}

    rows = conn.execute(
        """
        SELECT id FROM potential_dupes
        WHERE (paper_id_1 = ? AND paper_id_2 = ?) OR (paper_id_1 = ? AND paper_id_2 = ?)
        """,
        (paper_id_1, paper_id_2, paper_id_2, paper_id_1),
    ).fetchall()
    ids = [r[0] for r in rows]
    conn.executemany(
        """
        UPDATE potential_dupes
        SET same_paper = 1, same_author = NULL, earlier_paper_id = NULL,
            later_paper_id = NULL, later_cites_earlier = NULL
        WHERE id = ?
        """,
        [(i,) for i in ids],
    )
    conn.commit()
    print(f"  marked {len(ids)} candidate(s) between these two papers as same_paper "
          f"(paper_id {paper_id_1} and {paper_id_2} are the same underlying paper)")
    return set(ids)


def wrap(text, kind, use_color):
    if not text:
        return text
    if use_color:
        return f"{RED if kind == 'del' else GREEN}{text}{RESET}"
    left, right = ("[-", "-]") if kind == "del" else ("{+", "+}")
    return f"{left}{text}{right}"


def render_word_diff(text_a, text_b, use_color):
    """Each side's FULL text, word-diff-highlighted against the other side but
    rendered separately (not interleaved git-word-diff style) -- unchanged
    words plain, words that differ from the other side in red (side a) or
    green (side b). Returns (rendered_a, rendered_b)."""
    tokens_a = WORD_RE.findall(text_a)
    tokens_b = WORD_RE.findall(text_b)
    sm = difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
    out_a, out_b = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out_a.append("".join(tokens_a[i1:i2]))
            out_b.append("".join(tokens_b[j1:j2]))
        else:
            if i1 != i2:
                out_a.append(wrap("".join(tokens_a[i1:i2]), "del", use_color))
            if j1 != j2:
                out_b.append(wrap("".join(tokens_b[j1:j2]), "ins", use_color))
    return "".join(out_a), "".join(out_b)


def clear_screen():
    """ANSI clear-screen + move cursor home (+ purge scrollback via \033[3J on
    terminals that support it, e.g. xterm) so each candidate starts on a
    blank screen instead of scrolling past the previous one -- only called
    for an actual tty (see main()), never when output is piped/redirected."""
    sys.stdout.write("\033[H\033[2J\033[3J")
    sys.stdout.flush()


def print_candidate(row, use_color, index, total):
    # a = earlier/"original", b = later/"copy" when chronology is known; otherwise
    # paragraph_id_1/2 order, since which side is the original is genuinely unknown.
    if row["earlier_paper_id"] == row["paper_id_2"]:
        a, b = 2, 1
    else:
        a, b = 1, 2

    def field(name, side):
        return row[f"{name}{side}"]

    lcs = f"{row['lcs_ratio']:.2f}" if row["lcs_ratio"] is not None else "n/a"
    ngram = f"{row['ngram_jaccard']:.2f}" if row["ngram_jaccard"] is not None else "n/a"
    header = (
        f"[{index + 1}/{total}] potential_dupes id={row['id']}  similarity={row['similarity']:.3f}  "
        f"lcs_ratio={lcs}  ngram_jaccard={ngram}\n"
        f"  same_paper={bool(row['same_paper'])}  same_author={row['same_author']}  "
        f"later_cites_earlier={row['later_cites_earlier']}  status={row['status']}"
    )
    print()
    print(f"{BOLD}{header}{RESET}" if use_color else header)
    if row["status"] in AGENT_DECIDED_STATUSES:
        banner = (
            f"  \U0001F916 AGENT VERDICT ({row['status']}) -- you are auditing an agent's call, "
            f"not reviewing this fresh. A d/f/b/c/p/u press here overwrites it with your own "
            f"(human) verdict."
        )
        print(f"{YELLOW}{BOLD}{banner}{RESET}" if use_color else banner)

    a_line = f"--- a/{field('path', a)} ¶{field('idx', a)}  ({field('title', a)}, {field('year', a)})"
    b_line = f"+++ b/{field('path', b)} ¶{field('idx', b)}  ({field('title', b)}, {field('year', b)})"
    text_a, text_b = render_word_diff(field("text", a), field("text", b), use_color)
    print(f"{RED}{a_line}{RESET}" if use_color else a_line)
    print()
    print(text_a)
    print()
    print(f"{GREEN}{b_line}{RESET}" if use_color else b_line)
    print()
    print(text_b)


def read_key():
    """One keypress, no Enter required -- if stdin isn't a tty (piped input, e.g. in a
    test), fall back to reading a line and using its first character."""
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line[0] if line else "q"
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def prompt_action():
    prompt = ("  (d)upe  (f)alse-positive  (b)oilerplate  (c)itation  (p)apers-are-the-same  "
              "(u)nsure  (s)kip  (q)uit  (?)help > ")
    sys.stdout.write(prompt)
    sys.stdout.flush()
    while True:
        ch = read_key()
        sys.stdout.write(ch + "\n")
        if ch in ("d", "f", "b", "c", "p", "u", "s", "q"):
            return ch
        if ch in ("?", "h"):
            print("    d = confirmed duplicate   f = false positive   u = unsure")
            print("    b = boilerplate (also marks every other candidate sharing this")
            print("        pair's LSH bucket cluster as boilerplate -- see the module docstring)")
            print("    c = shared citation/reference text (same cascade as b, status='citation')")
            print("    p = these are two records of the same paper, not a real cross-paper")
            print("        match (also corrects same_paper=1 on every other candidate between")
            print("        this same pair of papers -- see mark_same_paper())")
            print("    s = skip (leave unreviewed, see it again next time)   q = quit session")
        else:
            print("    unrecognized key")
        sys.stdout.write(prompt)
        sys.stdout.flush()


def print_summary(conn):
    total = conn.execute("SELECT COUNT(*) FROM potential_dupes").fetchone()[0]
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM potential_dupes GROUP BY status"))
    cross = conn.execute("SELECT COUNT(*) FROM potential_dupes WHERE same_paper = 0").fetchone()[0]
    same_author = conn.execute("SELECT COUNT(*) FROM potential_dupes WHERE same_author = 1").fetchone()[0]
    cited = conn.execute("SELECT COUNT(*) FROM potential_dupes WHERE later_cites_earlier = 1").fetchone()[0]
    checked = conn.execute("SELECT COUNT(*) FROM potential_dupes WHERE lcs_ratio IS NOT NULL").fetchone()[0]
    high_overlap = conn.execute(
        "SELECT COUNT(*) FROM potential_dupes WHERE lcs_ratio >= 0.5 OR ngram_jaccard >= 0.5"
    ).fetchone()[0]
    agent_reviewed = sum(by_status.get(s, 0) for s in AGENT_DECIDED_STATUSES)
    print(f"{total} candidate(s) total in potential_dupes")
    print(f"  by status: {by_status}")
    if agent_reviewed:
        print(f"  agent-reviewed (not yet human-audited): {agent_reviewed} -- "
              f"see them with --agent-reviewed")
    print(f"  cross-paper: {cross}  same_author=1: {same_author}  later_cites_earlier=1: {cited}")
    print(f"  text-overlap checked (lcs_ratio/ngram_jaccard not NULL): {checked}/{total}"
          + (f"  ({high_overlap} with lcs_ratio>=0.5 or ngram_jaccard>=0.5)" if checked else ""))
    if checked < total:
        print(f"  {total - checked} candidate(s) predate the text-overlap check or haven't been "
              f"through build_dupe_candidates.py since it was added -- run it again to backfill them")


def parse_args():
    parser = argparse.ArgumentParser(description="Interactively review potential_dupes candidates.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--summary", action="store_true", help="print counts and exit, no review loop")
    parser.add_argument("--status", default="unreviewed",
                         help="only review candidates with this status (default unreviewed); "
                              "comma-separated for several, e.g. 'boilerplate,citation'; "
                              "pass '' to include every status")
    parser.add_argument("--order", choices=["similarity", "paper"], default="similarity",
                         help="'similarity' (most-confident first, default) or 'paper' "
                              "(go through one paper's candidates at a time)")
    parser.add_argument("--min-similarity", type=float, default=None)
    parser.add_argument("--author", help="only candidates involving an author whose name matches (substring)")
    parser.add_argument("--paper", help="only candidates involving a paper whose title matches (substring)")
    parser.add_argument("--cross-paper-only", action="store_true", help="skip same-paper candidates")
    parser.add_argument("--same-author-only", action="store_true")
    parser.add_argument("--different-author-only", action="store_true",
                         help="only candidates where the two papers do NOT share an author "
                              "(same_author = 0; excludes same-paper candidates too, where "
                              "same_author is NULL rather than meaningfully 0 or 1)")
    parser.add_argument("--cited-only", action="store_true", help="only later_cites_earlier = 1")
    parser.add_argument("--uncited-only", action="store_true", help="only later_cites_earlier = 0")
    parser.add_argument("--min-lcs-ratio", type=float, default=None,
                         help="only candidates with a longest-verbatim-word-run ratio >= this "
                              "(text_overlap.py; more accurate than embedding similarity alone, "
                              "but only set for candidates build_dupe_candidates.py has checked)")
    parser.add_argument("--min-ngram-jaccard", type=float, default=None,
                         help="only candidates with word n-gram overlap >= this (text_overlap.py)")
    parser.add_argument("--require-overlap-checked", action="store_true",
                         help="only candidates that actually have an lcs_ratio/ngram_jaccard value "
                              "(excludes ones from before that check existed / a re-run since)")
    parser.add_argument("--min-title-similarity", type=float, default=None,
                         help="only candidates whose two papers' titles are at least this similar "
                              "(difflib.SequenceMatcher ratio, 0..1 -- the same check and default "
                              "0.82 acceptance threshold retrieve_papers.py uses for a Crossref title "
                              "match). Useful for finding the (p)apers-are-the-same case -- two "
                              "`papers` rows that are actually the same paper retrieved twice")
    parser.add_argument("--max-title-similarity", type=float, default=None,
                         help="only candidates whose two papers' titles are at most this similar -- "
                              "the inverse of --min-title-similarity, for filtering OUT the "
                              "papers-are-probably-the-same case instead of isolating it")
    parser.add_argument("--min-english-score", type=float, default=None,
                         help="filter out candidates whose paragraph text doesn't look English -- "
                              "fraction (0..1) of each paragraph's words that are common English "
                              "words, no language-detection library involved (see english_score()); "
                              "both paragraphs must clear the bar. 0.1 is a reasonable starting "
                              "point: real English text usually scores well above it, other "
                              "languages (even Latin-alphabet ones) usually score near 0")
    parser.add_argument("--limit", type=int, default=None, help="stop after reviewing this many pairs")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--no-clear", action="store_true",
                         help="don't clear the screen between candidates (default: clear, tty only)")
    parser.add_argument("--agent-reviewed", action="store_true",
                         help="shortcut for --status set to every agent_decided_* value -- browse/audit "
                              "what agents have already reviewed instead of --status's default "
                              "'unreviewed'. Combine with the usual filters (--paper, --min-similarity, "
                              "etc.) same as any other review session; a d/f/b/c/p/u press overwrites "
                              "the agent's verdict with your own, same as it would for any other status.")
    parser.add_argument("--agent-verdict", nargs=2, metavar=("ID", "KEY"),
                         help="non-interactive: apply one verdict to potential_dupes id ID as KEY "
                              "(d/f/b/c/p/u, same letters as the interactive prompt) using the "
                              "agent_decided_* status vocabulary (AGENT_ACTIONS), then exit -- no "
                              "review loop, no tty needed. For an agent working through an assigned "
                              "batch of candidate ids: fetch each id's text via --library-db queries "
                              "or load_candidate_by_id(), decide per REVIEWING.md, then call this once "
                              "per id. Prints the candidate (same rendering as the interactive view) "
                              "and a confirmation line before exiting.")
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    conn.row_factory = sqlite3.Row
    conn.create_function("title_similarity", 2, title_similarity, deterministic=True)
    conn.create_function("english_score", 1, english_score, deterministic=True)

    if args.summary:
        print_summary(conn)
        conn.close()
        return

    if args.agent_verdict:
        candidate_id_str, key = args.agent_verdict
        try:
            candidate_id = int(candidate_id_str)
        except ValueError:
            print(f"--agent-verdict: {candidate_id_str!r} is not a valid potential_dupes id")
            conn.close()
            sys.exit(1)
        row = load_candidate_by_id(conn, candidate_id)
        if row is None:
            print(f"--agent-verdict: no potential_dupes row with id={candidate_id}")
            conn.close()
            sys.exit(1)
        if key not in ("d", "f", "b", "c", "p", "u"):
            print(f"--agent-verdict: {key!r} is not a valid verdict key (must be one of d/f/b/c/p/u)")
            conn.close()
            sys.exit(1)
        print_candidate(row, sys.stdout.isatty() and not args.no_color, 0, 1)
        marked = apply_agent_verdict(conn, row, key)
        label = AGENT_ACTIONS.get(key, "same_paper correction (no status set)")
        print(f"\n  applied {key!r} -> {label}: {len(marked)} potential_dupes row(s) updated")
        conn.close()
        return

    if args.agent_reviewed:
        args.status = ",".join(AGENT_DECIDED_STATUSES)

    rows = load_candidates(
        conn, args.status or None, args.author, args.paper, args.min_similarity,
        args.cross_paper_only, args.same_author_only, args.different_author_only,
        args.cited_only, args.uncited_only,
        args.min_lcs_ratio, args.min_ngram_jaccard, args.require_overlap_checked,
        args.min_title_similarity, args.max_title_similarity, args.min_english_score, args.order,
    )
    if not rows:
        print("No candidates match these filters. Try --summary to see what's there, "
              "or run build_dupe_candidates.py first.")
        conn.close()
        return

    use_color = not args.no_color and sys.stdout.isatty()
    do_clear = not args.no_clear and sys.stdout.isatty()  # never inject escape codes into piped/log output
    processed = 0  # candidates shown this session, d/f/u/b/c/p/s all count -- what --limit bounds
    reviewed = 0  # candidates actually given a d/f/u/b/c/p verdict -- skips don't count as progress
    # potential_dupes ids a (b)/(c) or (p) sweep already resolved this session -- don't re-present them
    resolved = set()
    shown_any = False  # don't clear before the very first candidate, only between them
    for i, row in enumerate(rows):
        if row["id"] in resolved:
            continue

        if args.limit is not None and processed >= args.limit:
            print(f"\nhit --limit {args.limit}; {len(rows) - i} more still {args.status or 'matching'} ")
            break

        if do_clear and shown_any:
            clear_screen()
        shown_any = True
        print_candidate(row, use_color, i, len(rows))
        action = prompt_action()
        if action == "q":
            break
        processed += 1
        if action == "s":
            continue

        if action == "b":
            marked = mark_boilerplate(conn, row)  # commits internally, cascades to the bucket cluster
            resolved |= marked
            reviewed += len(marked)
            continue

        if action == "c":
            marked = mark_citation(conn, row)  # commits internally, cascades to the bucket cluster
            resolved |= marked
            reviewed += len(marked)
            continue

        if action == "p":
            marked = mark_same_paper(conn, row)  # commits internally, cascades to the paper pair
            resolved |= marked
            reviewed += len(marked)
            continue

        conn.execute(
            "UPDATE potential_dupes SET status = ?, reviewed_at = ? WHERE id = ?",
            (ACTIONS[action], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), row["id"]),
        )
        conn.commit()  # commit per-decision, not batched -- quitting any time loses nothing
        reviewed += 1

    print(f"\nreviewed {reviewed} pair(s) this session.")
    conn.close()


if __name__ == "__main__":
    main()
