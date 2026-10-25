# Reporting a later article with several sources (proposal)

Status: proposal, written by the gi-cases agent on 2026-10-23 for the code session. **Implemented
(2026-10-25): the comparison page.** `gi_sources.py` reads `sources.json` and `gi_report_case.py` puts every
saved text source on the comparison page (site page and email attachment): an overview table with each
source's share and the union, then each source's passages; `cases.json` gets `sources`/`union_pct` and the
index shows the union. Still not implemented: option A's wording in the PubPeer text and emails (subject,
first sentence, union-based severity) and the per-source drafts.

**Decided by the user (2026-10-23): build option A.** Answers to the open questions at the end: yes to
all three. So option A also needs the per-source drafts from option B's email table (`-authors-sN`,
`-source-publisher-sN`, and the opt-in note to a references-only source), but not the source map or the
references panel. GI-518's drafts were written by hand for now (`data/dupe_publisher/GI-518/manual/`);
use them as the model for the wording. The `trace-other-sources` skill (`.claude/skills/trace-other-sources/SKILL.md`) finds and
verifies the extra sources; this file is about how the reports should present them.

## The problem

Every report is built around one pair: "the later article appears to reuse text from an earlier
article". GI-518 showed a different shape. The later article (a 2010 review) takes:

- 16% of its words from the paired earlier article (Introduction);
- at least 24% from an uncited 2005 review it isn't paired with (toxicology sections, two tables,
  24 references, the conclusion);
- 20 references, in order, from an unrelated 2009 cancer paper (reference list only, no text).

That's about 40% traced across two text sources plus a third for references. Today the only way the
second and third sources reach the PubPeer comment and the emails is as ticked findings, after a headline
that says 16% and "an earlier article". The headline understates the case, the comparison page and the
longest-passage image show only the paired source, and no draft goes to the other sources' authors or
publishers.

## Option A: no new pages, better wording (small change)

Keep one comparison page, but let the case's lead figure and wording reflect the extra sources.

1. **`data/dupe_publisher/GI-NN/sources.json`**, written by the agent (it's inside the case folder, so the
   background agent may already write it):
   ```json
   {"sources": [
     {"doi": "10.xxxx/...", "title": "...", "journal": "...", "year": 2005,
      "kind": "text",          // "text" | "references" | "tables" (a list if several)
      "fulltext": "source-1.txt",   // saved in the case folder; needed for kind "text"
      "cited": false, "shared_authors": false,
      "checked": "YYYY-MM-DD"}
   ]}
   ```
   The paired earlier article is implicitly source 0 and isn't listed.
2. **`gi_report_case.py` measures every `kind: "text"` source** against the later article with the GI
   settings (10-word shingles, x-drop 3): per-source share, union share, overlap between sources. It
   adds them to the measured findings, which are generated, not ticked:
   - "About 40% of the later article's text is also found in two earlier articles by other authors:
     16% in [paired earlier article] (2008) and 24% in [source 1] (2005). The later article cites
     neither."
   - "Twenty of its references (refs 3-22) are those of [source 2] (2009), an article on an unrelated
     topic, in nearly the same order." (from `kind: "references"`, with the count supplied by the agent)
3. **Subject and first sentence** switch to the plural when there's at least one text source:
   `Text from two earlier articles in "<title>"`. Extracts: the 3 longest passages *per source*, each
   labelled with its source.
4. **Severity** uses the union share. Recipients don't change.

Cost: `sources.json` parsing, one extra matcher call per source, wording branches in `gi_contact.py` and
`gi_pubpeer.py`. No new pages. The other sources' authors and publishers still get no draft.

## Option B: a multi-source report (full change)

Option A plus pages and drafts built for several sources.

### Comparison page: a source map

- **Top:** the later article's metadata, then a table, one row per source: title, year, journal, DOI,
  kind, share of the later article, longest passage, cited (yes/no), shared authors (yes/no). Last row:
  "Not traced: N%". The union figure is the headline.
- **Source map:** the later article end to end (the `--whole-document` layout, one column). Each matched
  run is highlighted in its source's colour, with a legend; untraced text stays plain. This shows at a
  glance which sections came from where, which per-source exhibit lists can't. Where two sources
  match the same words (none in GI-518), use the earlier source's colour and note the overlap.
- **Per source:** the existing exhibit view (passages side by side, differences marked) in a
  collapsible section per source.
- **References panel:** the later article's reference list, each entry marked with the source whose
  reference list contains it (same colours). Consecutive runs from one source get a bracket ("refs
  3-22: as in [source 2], same order"). Needs the agent to supply the matched entries in
  `sources.json` (`"refs": {"3": "Candelaria 2006", ...}`): matching reference strings automatically is
  unreliable, and the agent has already checked them by hand.
- **Images:** `GI-NN-longest.jpg` per text source (`GI-NN-longest-s1.jpg`), and the title-page image row
  extended to every source with a PDF. Sources available only as HTML get no PDF crops. Fall back to the
  text excerpt, as the screenshot renderer already does for sides it can't place.

### PubPeer comment

```
About 40% of this article's text also appears in two earlier articles by other authors, neither cited:

- 16%: <paired earlier article> (2008), doi:... (Introduction; 13 passages, longest 143 words)
- 24%: <source 1> (2005), doi:... (sections on tolerable daily intake, arsenic, cadmium, lead, mercury;
  Tables 1-2; 47 passages, longest 68 words)

References 3-22 are the references of <source 2> (2009), doi:..., an article on cancer chemotherapy, in
nearly the same order.

<ticked findings>

Comparison: <site URL>
```

### Emails

Recipients become per source. For a high-severity case (by union share or `[data]`):

| Draft | To | Says |
|---|---|---|
| `-publisher` | later journal | every source, every finding |
| `-institution` (one per institution, as now) | later authors' institutions | every source, every finding |
| `-authors-sN` | source N's corresponding author | the overview line, then its own source's figures, extracts and findings |
| `-source-publisher-sN` | source N's journal | same as `-authors-sN` |

- Source 0 (the paired article) keeps today's filenames (`-authors`, `-source-publisher`), so existing
  cases are unchanged.
- `kind: "references"`-only sources get a short note rather than the full draft ("your reference list
  appears in ..."): no text of theirs was taken. Make that draft opt-in (`"notify": true`).
- **Findings per source:** let a finding carry a source tag the way `[data]` works, `[s1] ...`. Untagged
  findings go everywhere. Tagged ones go to the journal and institution drafts and to that source's own
  drafts only. The tag is stripped when published, like `[data]`.
- **Subject:** `Text from your article in "<later title>"` for source drafts. For the later journal:
  `Text from two earlier articles in "<later title>" (doi:...)`.
- **Attachments:** the one comparison page (it covers all sources) plus that source's longest-passage
  image.
- **Contacts:** `contacts.json` gains `"sources": {"s1": {"to": ..., "names": [...], "source": ...,
  "checked": ...}}`, the same shape as `authors`; `journal_contacts.json` already keys by journal, so
  source journals just get entries there.

### Index and site

`cases.json` gains `sources` (count) and `union_pct`. The index shows the union share and a "2 sources"
badge. Keep the GI pair as the case's identity (GI-NN): sources are found during review, not by the index.

### Tests to add (code session)

- Union and overlap arithmetic, including two sources matching the same words.
- Draft set and filenames for 0, 1 and 2 extra sources, high and low severity; source-0 filenames unchanged.
- `[sN]` routing and stripping, alongside `[data]`.
- A `kind: "references"` source produces no text measurement and no draft unless `notify`.

## Recommendation

Do option A first: it fixes the understated headline and wording in every report, and `sources.json`
is what option B needs anyway. Build the source map and per-source drafts once a second case like
GI-518 turns up. The skill should make that more likely, since it gives the gi-cases agent a routine for
looking.

## Decisions (2026-10-23)

- A source the index didn't pair counts toward severity: severity uses the union share.
- An uncited text source's authors get the same email as the paired article's authors (and its
  journal the same as the source publisher's).
- A references-only source's authors are told too, with a shorter note.
