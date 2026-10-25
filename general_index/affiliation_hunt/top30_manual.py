"""Pairs from run top30_s16 that a browser download would complete: every missing side was blocked
(403/202/429/network/503) or landed on an HTML page, not paywalled or 404. Writes the list_manual_downloads.py
format (so import_manual_downloads.py takes it back) to data/verify_top30_s16/manual_downloads/."""
import html, json, pickle, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "/home/arthur/Sites/dupefinder")
import list_manual_downloads as lmd
S, W = Path(sys.argv[1]), Path("data/verify_top30_s16")
short = pickle.load(open(S / "top30_short.pkl", "rb"))
st = sqlite3.connect(W / "state.sqlite3")
cols = ("key", "title", "authors", "year", "doi", "status", "error", "pdf_url")
rows = {(r[4] or "").lower(): dict(zip(cols, r)) for r in st.execute(f"select {','.join(cols)} from papers")}
def fetchable(p):
    if p["status"] == "oa_url_not_pdf":
        return True
    return p["status"] == "error" and "404" not in (p["error"] or "")
pw = W / "manual_downloads" / "paywalled.txt"   # ranks the user found paywalled by hand
paywalled = {int(t.split("#")[0]) for t in (pw.read_text().splitlines() if pw.exists() else []) if t.split("#")[0].strip()}
pairs, manifest, copy_only = [], {}, 0
for rank, r in enumerate(short, 1):
    if r["fulltext"] is not None or rank in paywalled:
        continue
    sides = {s: rows.get(r[s][0].lower()) for s in "ab"}
    if any(v is None for v in sides.values()):
        continue
    missing = {s: p for s, p in sides.items() if p["status"] != "downloaded"}
    if not missing:
        continue
    if all(fetchable(p) for p in missing.values()):
        pairs.append((rank, r, sides, missing))
        for p in missing.values():
            p["title"] = lmd.strip_html(p["title"])
            manifest[lmd.suggested_filename(p)] = {k: p[k] for k in cols if k != "pdf_url"}
    elif r["copy_side"] and r["copy_side"][0] in missing and fetchable(missing[r["copy_side"][0]]):
        copy_only += 1
out = W / "manual_downloads"; out.mkdir(exist_ok=True)
(out / "_manifest.json").write_text(json.dumps(manifest, indent=2))
def why(p):
    if p["status"] == "oa_url_not_pdf":
        return "landing page, not a PDF"
    e = (p["error"] or "").replace("download failed: ", "")
    return "host timed out / unreachable from here" if e.startswith("network error") else e
import re, unicodedata
gi = sqlite3.connect("file:data/gi.sqlite3?mode=ro", uri=True)
plain = lambda t: unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
surn = lambda ns: {w for n in ns for w in re.findall(r"[a-z]{3,}", plain(n))}
def authors_differ(r):
    """Self-reuse check the index's own shared-author flag missed (name order/spelling variants): any name
    word shared between the two papers' OpenAlex + index author lists, or the top-30 author's surname on the
    earlier paper. Two downloads (#98, #116) were exactly this."""
    ld = gi.execute("select author_a, author_b, doi_a from lead_details where pair_id=?", (r["pair_id"],)).fetchone()
    idx = {"a": ld[0], "b": ld[1]} if ld[2].lower() == r["a"][0].lower() else {"a": ld[1], "b": ld[0]}
    later = r["copy_side"][0]; earlier = "ab"[later == "a"]
    names = lambda s: [n for (n,) in gi.execute("select name from oa_authorships where doi=?", (r[s][0].lower(),))]         + [n for n in (idx[s] or "").split(";") if n.strip()]
    E, L = surn(names(earlier)), surn(names(later))
    top = {plain(n).split()[-1] for g in r["top30"] for n, _, _ in g}
    if top & E:
        return False, "same top-30 author on the earlier paper (self-reuse?)"
    if E & L:
        return False, "shared name on both papers: " + ", ".join(sorted(E & L)[:3]) + " (self-reuse?)"
    co = coauthors.get(str(rank_of[r["pair_id"]]), {})
    if (co.get("count") or 0) >= 2:
        return False, f"the two sides' authors have {co['count']} papers together (collaborators)"
    if co.get("count") is None and co.get("note"):
        return True, "different authors (" + co["note"] + ")"
    return True, "different authors" if E else "earlier paper's authors unknown"
cf = W / "manual_downloads" / "coauthors.json"   # top30_coauthors.py: joint OpenAlex works between the sides
coauthors = json.load(open(cf)) if cf.exists() else {}
rank_of = {r["pair_id"]: i for i, r in enumerate(short, 1)}
pairs = [(rank, r, sides, missing, authors_differ(r)) for rank, r, sides, missing in pairs]
pairs.sort(key=lambda x: (not x[4][0], x[1]["check"] != "on paper", x[0]))
md = [f"# Top-30 shortlist: blocked downloads that would complete a pair", "",
      f"{len(pairs)} pairs ({len(manifest)} PDFs). Each missing paper has an open-access copy the automated download "
      "was refused (HTTP 403/202 = publisher bot-blocking, 429/503/network = rate-limit or flaky host) or that "
      "pointed at a landing page; a normal browser usually gets these. Paywalled (no OA copy) and 404 papers are left out.",
      "", "Save each PDF into this directory under the exact filename given, then:", "",
      "```", "cd data/verify_top30_s16", "../../../.venv/bin/python3 ../../../import_manual_downloads.py",
      "```", "and ask Claude to re-run extract / load-text / measure / pages for run top30_s16.", "",
      "Pairs whose two papers have different authors first (two downloads so far were one group reusing its own text "
      "under a differently written name), then those printing the top-30 affiliation, then model-score order (rank in data/top30_candidates.tsv). 'on paper' = the copying paper prints the "
      "top-30 affiliation; 'same country' is much weaker (often an OpenAlex name merge).", ""]
hp = ["<h1>Top-30 shortlist: blocked downloads</h1><p>Save each PDF here under the filename shown, then run "
      "import_manual_downloads.py in data/verify_top30_s16. Pairs in model-score order.</p>"]
for rank, r, sides, missing, (differ, note) in pairs:
    who = "; ".join(f"{n} ({u})" for grp in r["top30"] for n, u, _ in grp[:2])
    later = r["copy_side"][0] if r["copy_side"] else None
    head = f"## #{rank} · p={r['p']:.2f} · {r['check']} · {who} · {note}"
    md.append(head); hp.append(f"<h2>{html.escape(head[3:])}</h2><ul>")
    for s in "ab":
        p, role = sides[s], ("copying (later)" if s == later else "earlier")
        if p["status"] == "downloaded":
            md.append(f"- {role}: {p['year'] or ''} *{p['title'][:110]}* — already downloaded")
            hp.append(f"<li>{role}: {p['year']} <i>{html.escape(p['title'][:110])}</i> — already downloaded</li>")
            continue
        fn = lmd.suggested_filename(p)
        md += [f"- **{role}: {p['year']} {p['title'][:110]}**", f"  - https://doi.org/{p['doi']}",
               *( [f"  - OA copy tried: {p['pdf_url']}"] if p["pdf_url"] else []),
               f"  - failed: {why(p)}", f"  - save as: `{fn}`"]
        oa = f' · <a href="{html.escape(p["pdf_url"])}">OA copy tried</a>' if p["pdf_url"] else ""
        hp.append(f'<li><b>{role}: {p["year"]} {html.escape(p["title"][:110])}</b><br>'
                  f'<a href="https://doi.org/{html.escape(p["doi"])}">doi.org/{html.escape(p["doi"])}</a>{oa} · '
                  f'failed: {html.escape(why(p))}<br>save as: <code>{html.escape(fn)}</code></li>')
    md.append(""); hp.append("</ul>")
(out / "_pending.md").write_text("\n".join(md))
(out / "_pending.html").write_text("<!doctype html><meta charset=utf-8><title>Top-30 manual downloads</title>"
                                   "<body style='font-family:sans-serif;max-width:900px;margin:auto'>" + "".join(hp))
from collections import Counter
print(len(pairs), "pairs,", len(manifest), "PDFs; one missing PDF:", sum(len(x[3]) == 1 for x in pairs),
      "| by check:", Counter(r["check"] for _, r, *_ in pairs), "| authors differ:", sum(x[4][0] for x in pairs),
      "| copying paper fetchable but earlier paywalled/404:", copy_only)
print(Counter(why(p) for _, _, _, m, _ in pairs for p in m.values()).most_common(8))
