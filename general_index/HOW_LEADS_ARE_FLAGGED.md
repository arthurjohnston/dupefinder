# How a General Index pair becomes a lead

Written down 2026-10-11, after the user asked "we are flagging a pair when how many indices are shared by the
pair?". There is no single count: a pair has to pass four thresholds in turn, then the filters, then it is
given a tier.

## The thresholds

1. **Sampling** (`gi_sample_5grams.py`). Not every 5-gram is kept. Each one's lowercased text is hashed
   (blake2b) and kept only when `hash % SAMPLE == 0`, so the same 5-gram is kept or dropped in every
   document. 1 in 8 for the 16-shard run (1 in 4 for the first four slices). A sampled 5-gram that
   occurs in more than 20 documents (`gi_pairs.py --max-group 20`) is ignored as stock phrasing.
2. **At least 5 shared sampled 5-grams at 1 in 8, 10 at 1 in 4** (`gi_blocks.py`'s
   `MIN_SHARED_BY_RATE`; `gi_pairs.py --min-shared` defaults to 10). This is what makes two documents a
   pair at all: about 40 shared 5-grams in the full text at either rate. (Corrected 2026-10-12: this
   said 10 at 1 in 8, about 80 5-grams; FLOOR_EXPERIMENT.md found the discrepancy.)
3. **Both papers at least 25 sampled 5-grams long** (`gi_report.py --min-samples 25` at 1 in 8; 200 / rate
   in `run_gi.sh`), about 200 5-grams in full. Shorter documents get reason "too short": a handful of shared
   phrases would be a large share of almost nothing.
4. **The shared samples are at least 15% of one paper's sampled 5-grams** (`gi_rank_leads.py --min-pct 15`).
   This is the real cut-off for a lead, and the source of the 15-30%, 30-50%, ... bands. Pairs with an
   unidentified side (tier 5) need 50% (`--unidentified-min-pct 50`).

A pair that passes still goes through `gi_filters.py` (same work, shared authors, catalogue artifacts such
as one PDF under two DOIs or a conference supplement) before it is ranked.

So a typical lead shares hundreds of 5-grams; a short paper sharing about 40 still counts if that is 15% of
it. The index's 5-grams are lemmatized and skip windows that start or end on a stopword, so these counts
are not comparable with the full-text check (exact 10-word runs, `compare_two_papers.py`).

**What this misses.** Anything under the shared-sample minimum or under 15% never becomes a lead: lighter copying,
such as a page or two lifted into a long paper, mostly sits below 15%. Every estimate built on the leads
is a lower bound for that reason. The 15% floor was chosen on the slice 1 pilot (PILOT_FINDINGS.md,
"Verification"): 0 of 72 pairs under 15% shared any abstract text. That is an abstract-level test only;
body reuse under a different abstract would not show up in it. FLOOR_EXPERIMENT.md tests the floor on
full text: keep 15% for the general queue; below it, see its recommendation.

## The tiers (`gi_rank_leads.py`)

| tier | meaning | DOI pairs (2026-10-11) |
|---|---|---|
| 1 | a Retraction Watch retraction for plagiarism on one side | 1,493; test set only |
| 2 | the two abstracts share a 20+ word verbatim run (and aren't identical) | 21,903; all full-text checked |
| 3 | a Retraction Watch retraction for another reason on one side | 2,350; skipped (already retracted) |
| 4 | everything else the filters kept: index evidence only | 1,870,267 |
| 5 | one side unidentified (1.93M), or a near-identical copy, usually one PDF held twice (35k) | 1.97 million |

## Estimates (2026-10-11)

Copying of someone else's work only (cross-author or data copying), pairs not papers.

| tier | confirmable from the PDFs: low / best / high | behind paywalls: low / best / high |
|---|---|---|
| 2 | 300 / 410 / 490 | 2,800 / 7,500 / 9,300 |
| 4 | 200 / 1,000 / 6,000 | 3,000 / 30,000 / 170,000 |
| 5 | ~0 | can't estimate |

Tier 2: 1,137 of 21,903 pairs had both PDFs (5.2%); 665 of those are real overlap with no shared author on
the bylines (58%); 77% of the AI-reviewed ones are copying of others; the range allows for the AI being
wrong 5-40% of the time (no human check yet). Tier 4 rests on 2 cases in 49 checked pairs; the strat4_s16
sample (5,000 pairs) should give 150-200 checked pairs.

## Counts at each step (2026-10-11, 16 shards at 1 in 8)

| step | document pairs |
|---|---|
| pairs at all (>= 5 shared samples at 1 in 8, 10 at 1 in 4) | 117,217,664 |
| >= 15% on one side | 75,609,653 |
| ... and both sides >= 25 sampled 5-grams | 54,004,538 |
| ranked into tiers 1-5 after the filters | 4,888,077 (3,862,332 DOI pairs) |

So the 15% floor removes about a third of the pairs; the filters (shared authors, same work, catalogue
artifacts, neither side identified) remove most of the rest. Document pairs exceed DOI pairs because the
index holds some PDFs twice under one DOI; the tier table and the estimates count DOI pairs
(`primary_pair = 1`).

## Copying by a preprint goes last (2026-10-12)

`preprint_doi_prefixes.tsv` lists the DOI prefixes that preprint servers and repositories assign (arXiv,
SSRN, Zenodo, figshare and its institutional instances, ResearchGate, Research Square, Preprints.org, the
OSF family, ChemRxiv, Authorea, TechRxiv, Qeios, bioRxiv/medRxiv, JMIR Preprints, SciELO Preprints,
Cambridge Open Engage, Copernicus discussion papers, NBER working papers, ...), each with how it was
verified (Crossref `/prefixes` and `/members`, DataCite `/prefixes` and `/clients`, doi.org RA lookup) and
its count in the ranked leads. Where a prefix is shared with journals (10.1101 with the Cold Spring Harbor
journals, 10.2196, 10.5194, 10.7287, 10.1038, 10.1002, 10.1590, 10.3762, 10.14293, 10.3386) a suffix
regex picks the preprints. `gi_preprints.is_preprint_doi(doi)` reads it.

The rule (`gi_preprints.preprint_demotion()`): a pair whose **later** (copying) paper has a preprint DOI is
demoted to the end; a preprint that was copied **from** (the earlier side) is an ordinary case. When it is
unknown which came first (same or missing year) and either side is a preprint, the pair is "possibly by a
preprint": a milder demotion, after the ordinary pairs and before the known ones. Why: a preprint or
repository upload that repeats a paper is most often the authors' own copy or an aggregator's re-posting,
rarely a publishable finding. Nothing is hidden and no verdict changes. Applied in
`gi_review_cli.py` (queue order, plus a NOTE line), `gi_write_pages.py` index.html (order, plus a tag),
`gi_rank_leads.py` (sort key within a tier, and a note column in `--tsv`, now "priority"; takes effect at the next
ranking run, the leads table was not rewritten) and `gi_verify.py select` (within a tier, so a `--limit`
batch retrieves the rest first; the stratified calibration sample is left alone). "Earlier" is the page's
own order (`gi_write_pages.earlier_paper()`, library years) where a library exists, else the index's years.

Counts (2026-10-12, by the index's years): 33,962 of the 3,862,332 ranked DOI pairs have a preprint DOI on a
side (SSRN 20,019, bioRxiv/medRxiv 3,954, Copernicus discussions 3,369, Zenodo 2,768, NBER 2,149, ...):
2,540 on the later side, 2,158 on the earlier side only, 29,264 with the order unknown (mostly a missing
year: a 2022-update date is left blank). The GI predates most preprint DOIs (no arXiv 10.48550 DOIs at
all), so among the 721 result pages only 2 are affected (GI-50: a 2015 Zenodo upload of a 2011 paper, later
side; GI-322: Nature Precedings, same year).

### Copying by a major publisher goes first (2026-10-12)

The other end of the same ordering: when the **later** (copying) paper is from a major publisher, the pair goes
first. A copy that got into a major journal is the most consequential finding. One combined priority,
`gi_preprints.pair_priority(doi_a, doi_b, earlier)`, replaces the 0/1/2 demotion everywhere it was used
(`preprint_demotion()` is unchanged and still what the preprint half calls); lower sorts first:

| rank | when |
|---|---|
| -2 promoted | the later paper is from an enabled major publisher; also when the order is unknown but **both** sides are (whichever is later, it is one) |
| -1 possibly promoted | the order is unknown (same or missing year) and exactly one side is: the mild promotion |
| 0 ordinary | everything else, including a later paper in an excluded venue (below) |
| 1 possibly by a preprint | as before |
| 2 later paper is a preprint | as before |

The preprint rules are checked first and win. A preprint DOI is never a major-publisher DOI (SSRN is
Elsevier's prefix, Research Square and Nature Precedings Springer Nature's, ESSOAr Wiley's, ChemRxiv ACS's).
When the order is unknown and one side is a preprint, the pair stays "possibly by a preprint" even if the
other side is a major journal; the usual shape is the authors' own preprint of their journal paper. A
journal paper known to be later than a preprint is promoted.

**The list.** `major_publisher_doi_prefixes.tsv` has the columns prefix, suffix_regex, publisher,
crossref_member, imprint_notes, group, promote, reason, how_verified and count_in_leads. Each publisher's
prefixes are its Crossref `/members/{id}` `prefix` list (the member found by `/members?query=` and by
`/prefixes/{prefix}` on its best-known prefix, with the member's `primary-name` checked). That includes the
society journals each one publishes under their own prefixes. The `group` column decides what promotes
(`gi_preprints.ENABLED_GROUPS`):

- **core**, the publishers named in the request:
  - Elsevier (member 78; Cell Press and The Lancet are 10.1016);
  - Springer Nature (297 and 93; Springer 10.1007, Nature 10.1038, BMC 10.1186, Palgrave 10.1057, Adis 10.2165);
  - Wiley (311);
  - Taylor & Francis (Informa, 301 and 3197; Routledge 10.4324, CRC 10.1201);
  - SAGE (179);
  - IOP Publishing (266);
  - PLOS (340).
- **etc**, other widely recognised publishers and societies (drop the group to promote only the core):
  - OUP (286), CUP (56), ACS (316), RSC (292);
  - IEEE (263), ACM (320), AIP (317), APS (16);
  - AAAS (221), PNAS (341), the Royal Society (175);
  - BMJ (239), Massachusetts Medical Society (150), AMA (10);
  - Wolters Kluwer/Ovid incl. LWW (276), Karger (127), Thieme (194), De Gruyter (374), Emerald (140);
  - Annual Reviews (22), APA (15), ASM (235);
  - MIT Press (281), University of Chicago Press (200), EMBO Press (79), eLife (4374);
  - Johns Hopkins University Press. Its Crossref member is Project MUSE (147), which also hosts other
    university presses' journals, so 10.1353 is broader than JHU alone.
- **debatable**, off by default:
  - MDPI (1968), Frontiers (1965), Bentham (965), Inderscience (378);
  - Hindawi (98; its prefixes also sit under Wiley's member record): Wiley-owned since 2021, then mass
    paper-mill retractions;
  - Dove Medical Press (10.2147, under T&F's member) and Cureus (10.7759, under Springer Nature's);
  - Medknow (10.4103/10.6219/10.15380, under Wolters Kluwer's);
  - De Gruyter's Sciendo hosting service (10.2478).

  To try them: `pair_priority(..., groups={"core", "etc", "debatable"})`.
- **promote=no on a whole prefix**: a non-journal prefix that one of these members owns.
  - Preprint servers: SSRN, Research Square, Cambridge Open Engage, SAGE Advance, ChemRxiv, TechRxiv,
    Authorea.
  - Other non-journal prefixes: protocols.io, The Winnower, MIT Press blogs, IEEE StandardDocs, Adam Matthew
    Digital and a data hub.

  The preprints among them are already caught by `preprint_doi_prefixes.tsv`.

The list has 369 rows: 205 core and 113 etc prefixes that promote, 23 debatable, and 28 promote=no
(exclusions and non-journal prefixes).

**Exclusions: known paper-mill venues inside the major publishers.** Each is a `promote=no` row with a
suffix rule and a reason. A suffix-rule row is checked before its prefix's whole-prefix row, so it overrides
the promotion. A pair whose later paper is in one of these venues is tagged "later paper: IOP Publishing,
Journal of Physics: Conference Series (not promoted: ...)" and ranks **ordinary**: not promoted, not
demoted. These are exactly where copying shows up, so they are worth reviewing, but not as "a prestigious
journal printed a copy". The DOI patterns were checked against Crossref `/journals/{issn}` and its works.

- The ones named in the request:
  - IOP's J. Phys.: Conf. Ser. (`1742-6596/`);
  - IOP Conf. Ser.: Mater. Sci. Eng. (`1757-899x/`);
  - IOP Conf. Ser.: Earth Environ. Sci. (`1755-1315/`);
  - Elsevier's Materials Today: Proceedings (`j\.matpr\.`);
  - Springer's AISC (and its predecessor, Advances in Intelligent and Soft Computing), LNEE and LNNS.
    A Springer chapter DOI (10.1007/978-...-X_N) carries only the volume's ISBN, not the series, so a
    suffix regex cannot express this one. Its suffix_regex is `@major_publisher_excluded_books.tsv`
    instead, which lists all 5,138 volume DOIs of the four series from Crossref
    `/works?filter=issn:...,type:book`. That rule matches a volume and its chapters.
- Venues established from this database's `retraction_watch` table. The rule: at least 100 retractions
  for paper mill / compromised peer review / rogue editor, and at least 1.5% of the venue's Crossref DOI
  count.
  - Springer: Arabian Journal of Geosciences (`s12517-`, 775 = 6.3%), Soft Computing (`s00500-`, 773 =
    6.8%), Journal of Ambient Intelligence and Humanized Computing (`s12652-`, 430 = 8.6%), Cluster
    Computing (`s10586-`, 112 = 1.7%), and Tumor Biology's Springer years (`s13277-`).
  - SAGE: Tumor Biology's SAGE years (`10104283` / `14230380`, 158 in all = 2.1%), and the International
    Journal of Electrical Engineering & Education (`00207209`, 627 = 11%).
  - Wiley: International Wound Journal (`iwj.`, 302 = 5.9%) and Skin Research and Technology (`srt.`, 102
    = 3.1%).
  - ACM: the ICIMTECH 21 proceedings volume (`3465631.`, 323).
- Left out by the 1.5% bar: PLOS ONE (415 of ~300k), Environmental Science and Pollution Research,
  Multimedia Tools and Applications, Journal of Cellular Biochemistry, Biomedicine & Pharmacotherapy (each
  under 1%), and Optical and Quantum Electronics and Annals of Operations Research (1.0%).
- Left out because a DOI cannot tell the venue apart: AIP Conference Proceedings (300; it shares
  10.1063/5.0... with the AIP journals), and IEEE's conference proceedings.
- Never on the list: IOS Press's Journal of Intelligent & Fuzzy Systems (1,553; 10.3233 is not under any
  listed member).

**Where it applies.**

- `gi_review_cli.py`'s queue keeps the agent's grouping as the primary key: cross-author/data problem
  calls, then other problem calls, then the rest. A major-publisher pair the agent called "no problem"
  never jumps an ordinary pair it called cross-author copying. Within each group, promoted pairs come
  first, then possibly-promoted, then ordinary. Demoted (preprint) pairs still go last overall. Overlap
  descending is the tiebreak (`queue_key()`). There is a NOTE line for any tag.
- `gi_write_pages.py` index.html has no agent grouping (it is by score alone), so the priority leads there,
  then the score, with a tag.
- `gi_rank_leads.py`: within a tier, before book/shape/share. The years come from one pass over
  `documents`, because most leads now need them. The note goes in the `--tsv` "priority" column. This
  takes effect at the next ranking run; the leads table was not rewritten.
- `gi_verify.py select`: within a tier. The stratified sample is still untouched.

No verdict changes.

**Counts** (2026-10-12, read-only, by the index's years; DOI pairs = `primary_pair`).

Ranked leads, by tier:

| tier | DOI pairs | promoted (-2) | possibly promoted (-1) |
|---|---|---|---|
| 1 | 1,493 | 859 | 391 |
| 2 | 21,903 | 11,484 | 3,027 |
| 3 | 2,350 | 1,347 | 618 |
| 4 | 1,870,267 | 611,898 | 558,695 |
| 5 | 1,966,319 | 6,794 | 1,391,670 |

In tier 5 one side is unidentified, so the order is almost always unknown.

The publishers most often on the later side: Elsevier 127k, Springer Nature 122k, IEEE 63k, Wiley 55k, T&F
30k, IOP 17k. Distinct DOIs in the leads by publisher: Elsevier 463,672, Springer Nature 356,147, Wiley
222,491, IEEE 203,257, BMJ 103,390, T&F 91,481, CUP 76,098, OUP 67,982, SAGE 49,703, ... The per-prefix
DOI-pair counts are in the TSV.

The excluded venues keep 15,014 DOI pairs at ordinary that would otherwise be (possibly) promoted, mostly in
tiers 4/5:

- AISC/LNEE/LNNS 5,930;
- J. Phys.: Conf. Ser. 4,348;
- IOP MSE 1,976;
- IOP EES 1,015;
- Materials Today: Proc. 429;
- Soft Computing 392;
- Tumor Biology 326;
- Arabian J. Geosci. 236;
- Cluster Computing 160;
- JAIHC 109;
- Int. Wound J. 64;
- Skin Res. Technol. 21;
- IJEEE 8.

In tiers 1-3 that is 193 pairs. The non-journal prefixes (protocols.io, data hubs, ...) keep another 32 at ordinary.

Turning the debatable group on would promote another 25,066 DOI pairs and possibly-promote 67,137 more.

Full-text pairs: 13,034 of 30,865 promoted, 10,238 possibly promoted, and 119 tagged in an excluded venue
(63 of them J. Phys.: Conf. Ser.).

Result pages: 54 of the 721 promoted (38 later Springer Nature, mostly book chapters; 10 both sides Springer
Nature with the order unknown; 3 PLOS; 1 each CUP, JAMA, AIP/IEEE), and 19 possibly promoted. One is tagged
J. Phys.: Conf. Ser. and kept ordinary.


### All of it in one database: `data/priorities.sqlite3`

`gi_priority_db.py` rebuilds it from scratch, reading gi.sqlite3 read-only. It holds:
- the three lists as tables: `preprint_prefixes`, `major_publisher_prefixes`, `major_publisher_excluded_books`;
- `doi_class`: every DOI in the ranked leads or the full-text pairs that is a preprint or a major publisher's;
- `lead_priority`: every ranked lead's earlier side (by the index's years), priority and note;
- `fulltext_priority`: every full-text pair's priority, decided as on its page, with the AI's and the human's
  verdicts.

Priority -2 promoted, -1 possibly promoted, 0 ordinary, 1 possibly by a preprint, 2 later paper is a preprint.
Rerun it after the lists change or new pairs are checked.
