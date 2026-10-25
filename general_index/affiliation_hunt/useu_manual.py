"""Pairs from the US/European runs (useu*_s16) that a browser download would complete: every missing side was
blocked (403/202/429/network/503) or landed on an HTML page, not paywalled or 404. All of them already passed
useu_screen.py (no shared author id or surname, authors never published together), and the copying paper prints
a US/European institution. One folder for all runs, data/useu_manual_downloads/, in the list_manual_downloads.py
format; each manifest entry carries its run, so the import goes back to the right workdir.
Usage: useu_manual.py D [MAX_PAIRS]   (D: data/affiliation_hunt)"""
import html, json, pickle, sqlite3, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, "/home/arthur/Sites/dupefinder")
import list_manual_downloads as lmd
D, MAX = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 300
# useu_ranked_r1.pkl: the list rounds 1-2 ran from (the rebuild after rest4 dropped pairs already checked)
ranked = {((o["a"][0] or "").lower(), (o["b"][0] or "").lower()): o
          for f in ("useu_ranked_r1.pkl", "useu_ranked.pkl") for o in pickle.load(open(D / f, "rb"))}
gi = sqlite3.connect("file:data/gi.sqlite3?mode=ro", uri=True)
cols = ("key", "title", "authors", "year", "doi", "status", "error", "pdf_url")
def fetchable(p):
    if p["status"] == "oa_url_not_pdf":
        return True
    return p["status"] == "error" and "404" not in (p["error"] or "")
def why(p):
    if p["status"] == "oa_url_not_pdf":
        return "landing page, not a PDF"
    e = (p["error"] or "").replace("download failed: ", "")
    return "host timed out / unreachable from here" if e.startswith("network error") else e
runs = [r for (r,) in gi.execute("select distinct run from fulltext where run like 'useu%_s16'")]
pairs, seen = [], set()
for run in runs:
    st = sqlite3.connect(f"data/verify_{run}/state.sqlite3")
    rows = {(r[4] or "").lower(): dict(zip(cols, r)) for r in st.execute(f"select {','.join(cols)} from papers")}
    for a, b in gi.execute("select lower(doi_a), lower(doi_b) from fulltext where run = ? "
                           "and coalesce(ft_verdict, '') in ('', 'not both retrieved')", (run,)):
        o = ranked.get((a, b)) or ranked.get((b, a))
        sides = {"a": rows.get(a), "b": rows.get(b)}
        if o is None or None in sides.values() or (a, b) in seen:
            continue
        missing = {s: p for s, p in sides.items() if p["status"] != "downloaded"}
        if missing and all(fetchable(p) for p in missing.values()):
            seen.add((a, b))
            later = o["later"] if (o["a"][0] or "").lower() == a else "ab"[o["later"] == "a"]
            pairs.append((o, run, sides, missing, later))
pairs.sort(key=lambda x: (len(x[3]), -x[0]["p"]))       # one PDF to fetch first, then model score
total, pairs = len(pairs), pairs[:MAX]
out = Path("data/useu_manual_downloads"); out.mkdir(exist_ok=True)
manifest = {}
for o, run, sides, missing, later in pairs:
    for p in missing.values():
        p["title"] = lmd.strip_html(p["title"])
        manifest[lmd.suggested_filename(p)] = {**{k: p[k] for k in cols if k != "pdf_url"}, "run": run}
(out / "_manifest.json").write_text(json.dumps(manifest, indent=2))
inst = lambda o: "; ".join(sorted({f"{i} ({c})" for _, i, c in o["printed"]}))[:200]
intro = (f"{len(pairs)} of {total} pairs ({len(manifest)} PDFs), from runs {', '.join(sorted(runs))}. Each missing "
         "paper has an open-access copy the automated download was refused (HTTP 403/202 = publisher bot-blocking, "
         "429/503/network = rate-limit or flaky host) or that pointed at a landing page; a browser usually gets "
         "these. Paywalled and 404 papers are left out. Pairs needing one PDF first, then by model score. Every pair "
         "already passed the co-author screen; the institution shown is what the copying paper prints.")
md = ["# US/European runs: blocked downloads that would complete a pair", "", intro, "",
      "Save each PDF into this folder under the exact filename given, then ask Claude to import them "
      "(each goes back to its run's workdir) and measure.", ""]
hp = [f"<h1>US/European runs: blocked downloads</h1><p>{html.escape(intro)}</p><p>Save each PDF into "
      "<code>data/useu_manual_downloads/</code> under the filename shown, then ask Claude to import them.</p>"]
for n, (o, run, sides, missing, later) in enumerate(pairs, 1):
    head = f"#{n} · pair {o['pair_id']} · p={o['p']:.2f} · {inst(o)}"
    md.append(f"## {head}"); hp.append(f"<h2>{html.escape(head)}</h2><ul>")
    for s in "ab":
        p, role = sides[s], ("copying (later)" if s == later else "earlier")
        if p["status"] == "downloaded":
            md.append(f"- {role}: {p['year'] or ''} *{p['title'][:110]}* — already downloaded")
            hp.append(f"<li>{role}: {p['year']} <i>{html.escape(p['title'][:110])}</i> — already downloaded</li>")
            continue
        fn = lmd.suggested_filename(p)
        md += [f"- **{role}: {p['year']} {p['title'][:110]}**", f"  - https://doi.org/{p['doi']}",
               *([f"  - OA copy tried: {p['pdf_url']}"] if p["pdf_url"] else []),
               f"  - failed: {why(p)}", f"  - save as: `{fn}`"]
        oa = f' · <a href="{html.escape(p["pdf_url"])}">OA copy tried</a>' if p["pdf_url"] else ""
        hp.append(f'<li><b>{role}: {p["year"]} {html.escape(p["title"][:110])}</b><br>'
                  f'<a href="https://doi.org/{html.escape(p["doi"])}">doi.org/{html.escape(p["doi"])}</a>{oa} · '
                  f'failed: {html.escape(why(p))}<br>save as: <code>{html.escape(fn)}</code></li>')
    md.append(""); hp.append("</ul>")
(out / "_pending.md").write_text("\n".join(md))
(out / "_pending.html").write_text("<!doctype html><meta charset=utf-8><title>US/European manual downloads</title>"
                                   "<body style='font-family:sans-serif;max-width:900px;margin:auto'>" + "".join(hp))
print(f"{total} pairs completable by hand; listed {len(pairs)} ({len(manifest)} PDFs); one PDF missing: "
      f"{sum(len(x[3]) == 1 for x in pairs)}; p>=0.3: {sum(x[0]['p'] >= 0.3 for x in pairs)}")
print(Counter(run for _, run, *_ in pairs).most_common(), Counter(why(p) for x in pairs for p in x[3].values()).most_common(6))
