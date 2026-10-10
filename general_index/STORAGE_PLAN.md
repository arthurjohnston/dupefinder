# Storage plan: external drive for General Index lead PDFs

Briefing for a planning conversation (written 2026-10-07). Numbers are measured on shards 0-3 and
projected to all 16; firm figures come after the 16-shard pipeline run (step 2-3, ~1 day after the
last shard finishes downloading, see NEXT_STEPS.md).

## Measured PDF sizes

| Collection | PDFs | Size | Median | Mean |
|---|---|---|---|---|
| General Index lead papers (verify_leads12_s0123/papers) | 456 | 424 MB | 488 KB | 948 KB |
| anthropology corpus (anthropology/papers) | 84,208 | 180 GB | 524 KB | 2.2 MB (theses/books) |

Plan on 1-2 MB per paper.

## Leads now (shards 0-3, per DOI pair) and projection

Tiers (gi_rank_leads.py): 1 = Retraction Watch plagiarism retraction on one side (110), 2 = abstracts
share a 20+ word run (1,639), 3 = other retraction (129), 4 = index evidence only (104,377),
5 = partner unidentified (140,882).

Shards 0-3 cover 10 of 136 shard-pair blocks (~7%), so roughly 15x the leads at 16 shards, plus some
from the new step-2 settings (1-in-8 sampling, groups up to 100 documents: +70% pairs on shards 0-3).

| What gets downloaded | Pairs (16 shards, est.) | Papers | Open-access only (~13.5% succeed) | Every paper |
|---|---|---|---|---|
| Tiers 1-2 | ~26,000 | ~45,000 | ~6,000 PDFs, 6-12 GB | 45-90 GB |
| Tiers 1-4 | ~1.6M | ~2.5M | ~340,000 PDFs, 0.35-0.7 TB | 2.5-5 TB |

Retrieval success so far: 456 of 3,367 tier 1-2 papers (13.5%); 2,232 had no open-access copy, 473
errors, 193 not a PDF. Most strong leads are paywalled, not missing metadata.

## Other data that could live on the drive

- Shard samples (step 1 output): ~6.4 GB per shard at 1-in-8, ~105 GB for 16.
- gi.sqlite3: 12 GB at 4 shards; perhaps 100-150 GB at 16.
- gi_meta.sqlite3 (metadata, all 16 shards): check `du -h general_index/data/gi_meta.sqlite3`.
- Internal disk now: ~66 GB free of 915 GB (downloads still running).

## Recommendation so far

2 TB: holds every tier 1-2 paper even if all are obtained by hand, the open-access share of the
whole lead list, and the pipeline data with room to grow. 1 TB is the minimum. More than 2 TB only
pays if paywalled tier 3-4 papers are to be fetched in bulk (2.5M papers; not something to download
blind).

## Open questions for the user

- Which tiers are worth full text? (Tier 1-2 is the obvious set; tier 4 is large and mostly
  unverified.)
- Is there institutional/library access for paywalled papers, or open-access only?
- Should the pipeline data (samples, databases) move to the drive too, or only PDFs?
- Speed: an external USB SSD vs HDD (gi.sqlite3 and the pairing blocks do heavy random I/O; keep
  those on an SSD).

Paths: general_index/NEXT_STEPS.md (pipeline state), general_index/PILOT_FINDINGS.md (measurements).
Don't edit files under general_index/ from a planning session while the main session runs the pipeline.
