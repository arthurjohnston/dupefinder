# Bucketing / LSH variants for the General Index

Question: can a bucketing (LSH-style) scheme find heavy republication, partial reuse and
short-into-long containment across **all 16 slices (107M docs) in one streaming pass**, on this
machine (200 GB free disk, 30 GB RAM, 14 cores, ~24 MB/s from archive.org)?

**Answer: yes.** The sampler already running is itself an LSH scheme, and it is the right one
for containment. The limits are not in the bucketing. They are (1) disk for one pass's
samples and (2) the pair-counting step at full scale. Both can be solved, with the measured
numbers below. MinHash works too, but it does worse for the same storage on everything except
whole-document duplicates.

Every number is marked **[measured]** (with its script under `experiments/`) or **[est.]**.

---

## Ranked recommendations

1. **Keep consistent hash sampling, at mod-8 instead of mod-4, and change three things.**
   Compact each slice's samples once that slice finishes. Raise the bucket cap above 20 and
   weight shared samples by rarity. Count pairs in a three-phase job split into document
   blocks (see "Full-run design"). The full run is estimated at about 57 h of download, under
   about 80 GB of disk at peak, and about 8 GB of RAM.
2. **Rank on evidence (number of shared samples), not on containment alone.** A few copied
   paragraphs are a small fraction of either document, so a containment threshold throws them
   away. This is a ranking change, not a storage change. Biggest gain for the least work.
3. **Optional stage 0: the keyword table.** It's about 13x cheaper to download (about 4.4 h for
   all 16 slices), and could be a fast screen for whole-document duplicates. It can't find
   partial reuse.
4. **Don't adopt** MinHash banding with r ≥ 2, capped samples per document, partitioning by
   field or journal, a separate first pass to measure rarity, or a second pass to re-stream
   candidates. The reasons are below.

---

## 1. Synthetic test: which scheme finds what [measured]

`experiments/exp_synthetic.py`, `exp_mod8.py`. Documents are the real 5-gram sets of 3,064
documents from the first 150 MB of n-gram slice 3. Five kinds of planted pair, 40 of each:

- **T1, republication:** a copy of a document with 10% of its 5-grams swapped out.
- **T2, partial reuse:** S of one document's 5-grams added to another. S = 25, 50 or 100,
  meaning "a few paragraphs".
- **T3, short-into-long:** a document with 60–200 5-grams embedded whole in one with ≥ 2,000.

"Background" is the number of flagged pairs among the real, unmodified documents.

Thresholds are set so the underlying evidence is comparable across rates: ≥10 samples at
mod-4 and ≥5 at mod-8 both mean about 40 shared 5-grams.

| scheme | stored/doc | rule | T1 | T2 S=25 | T2 S=50 | T2 S=100 | T3 | background |
|---|---|---|---|---|---|---|---|---|
| mod-4 (current) | 180 | evidence ≥10 | 40/40 | 2/40 | **35/40** | **40/40** | **40/40** | 0 |
| mod-4 (current) | 180 | evidence ≥10, containment ≥0.2 | 40/40 | 0 | 4 | 18 | 40 | 0 |
| **mod-8** | **90** | **evidence ≥5** | **40/40** | 7/40 | **32/40** | **40/40** | **40/40** | **0** |
| mod-16 | 45 | evidence ≥3, containment ≥0.1 | 40/40 | 6 | 19 | 35 | 39 | 0 |
| capped (mod-4, M=128 kept per doc) | 97 | evidence ≥3, containment ≥0.2 | 40/40 | 0 | 3 | 22 | **22** | 9 |
| MinHash k=128, r=1 | 128 | ≥3 matching minima, containment ≥0.2 | 40/40 | 0 | 8 | 19 | 19 | 9 |
| MinHash k=256, r=1 | 256 | ≥3 matching minima, containment ≥0.2 | 40/40 | 0 | 5 | 21 | 30 | 6 |
| MinHash k=256, r=2 | 256 | ≥3 matching minima, containment ≥0.2 | 40/40 | 0 | 2 | 14 | 2 | 2 |
| MinHash k=128, b=32, r=4 | 128 | any | 40/40 | 0 | ≤1 | 0 | **0** | 0 |

Full grids are in `synthetic_results.txt` and `mod_rates.txt`.

What the table shows:

- **Whole-document republication is easy for every scheme.** That includes ordinary MinHash
  LSH, so for that one shape, MinHash would have worked fine.
- **Short-into-long is where Jaccard-based schemes fail.** A 150-5-gram document inside a
  5,000-5-gram one has Jaccard ≈ 0.03 but containment 1. Mod sampling keeps every one of the
  short document's samples, and all of them are in the long document, so T3 is 40/40.
  MinHash with r ≥ 2 finds 0–2. With r = 1 it finds 19–30, but stores 1.4–2.8 times more per
  document than mod-8.
- **Partial reuse is lost by containment thresholds, not by sampling.** Mod-4 finds 40/40 at
  S=100 on evidence ≥10 alone, but only 18/40 once containment ≥0.2 is also required. Rank on
  evidence.
- **Capping samples per document backfires.** Conditional random sampling (keep the M
  smallest hashes per document) saves storage only on long documents, and long documents are
  exactly the hosts in short-into-long reuse. Theses are the main source documents this
  project cares about (the dissertation that was copied in Saxby/Taro). T3 falls from 40/40 to
  22/40 at M=128. The top 1% of documents hold only 11.5% of the samples [measured,
  `exp_parts.py`], so the cap saves little anyway.
- **Background is boilerplate.** At ≥5 shared samples (mod-4) the background pairs share a
  Chinese thesis-template header, standard crystallography refinement text, or alphabet
  sequences from PDF junk (`fp_results.txt`). At ≥10 there are none. Weighting by rarity
  (§4) is the general fix.

A correction: an earlier version of this test counted plant artifacts as background. In the
republication plant, the swapped-in 10% comes from a second document, which creates an
unintended overlap pair. Those pairs are now excluded. The numbers above are the corrected
run.

### Was MinHash dismissed too quickly?

Partly.
- **Where it works:** whole-document duplicates (T1 40/40 everywhere), with storage that is
  constant per document.
- **Containment-tuned variants (LSH Ensemble, asymmetric LSH) can't beat the r = 1 rows
  above.** An r = 1 index already makes every shared minimum a candidate, so it is the most
  any banding of the same k signatures can recall.
- **The basic reason:** for A ⊂ B, a minimum hash coincides only with probability |A|/|B| per
  permutation. Every one of A's mod-p samples is in B. So for containment, mod sampling gets
  more matches per stored record, and the measured table shows it does.
- **b-bit or one-permutation MinHash** shrinks the signature but cannot fix that recall
  ceiling.

## 2. Storage for a one-pass run [measured + est.]

`experiments/exp_parts.py`, run read-only on the live slice 0 sample files (3.06M documents
seen so far).

- **Samples per document (mod-4):** median 128, mean 176, p99 1,056, p99.9 3,200 [measured].
  At 107M documents that's about 18.8B records at mod-4, or 9.4B at mod-8 [est.].
- **Raw size at 12 bytes per record:** 226 GB (mod-4), 113 GB (mod-8). Mod-4 doesn't fit.
- **Compaction** [measured on partition 0]: sort each partition, truncate the hash, delta-code
  the sorted hashes as varints, and store the document ID in 3 bytes (local to each slice).

  | hash bits kept | bytes/record (varint) | bytes/record (zlib) |
  |---|---|---|
  | 64 | 7.66 | 7.98 |
  | 48 | **5.66** | 6.37 |
  | 40 | 4.79 | 5.44 |

  - At 48 bits, false matches between different 5-grams are negligible: about 1e-4 of
    records [est.].
  - At 40 bits, about 1% of records collide with some unrelated 5-gram [est.]. That adds
    scattered single shared samples, which an evidence threshold of 5 or more ignores.
  - **Total, mod-8 at 48 bits: about 53 GB.** Mod-4 would be about 106 GB.
- **A separate rarity pass isn't needed.** How common a 5-gram is falls out of the merge:
  group size *is* its document frequency within the sample. A count-min or HyperLogLog first
  pass would cost a second full download (about 57 h) to learn something the merge gives for
  free.

## 3. How pairs grow toward the full corpus [measured + est.]

`experiments/exp_pair_scaling.py` uses all 64 partitions, with documents subsampled by ID.
`exp_heavy_shape.py` looks at the shape of the heavy pairs. Bucket cap 20, mod-4.

| docs | distinct pairs ≥1 | ≥5 | ≥10 | ≥50 |
|---|---|---|---|---|
| 96k | 1.39M | 2,828 | 680 | 69 |
| 192k | 3.80M | 6,520 | 1,924 | 257 |
| 385k | 10.07M | 16,393 | 5,016 | 998 |

- **Heavy pairs (≥50) grow about ×3.9 per doubling** [measured]. That's nearly f², which is
  what a fixed set of real pairs looks like under uniform subsampling. Extrapolating f² to
  107M documents gives **about 77M heavy pairs** [est.]. That is surprising, and it depends on
  the first 45% of slice 0 being representative of the whole.
- **Pairs at ≥10 grow sub-quadratically (×2.6–2.8 per doubling).** As the corpus grows, the
  cap of 20 starts dropping their semi-common 5-grams. Estimate at full scale: 16M–390M pairs.
- **Shape of the ≥50 pairs** [measured]:
  - 56% are twin-shaped (containment ≥0.8, sizes within 25%).
  - 3% are contained (containment ≥0.8, size ratio >2).
  - 14% are partial (containment <0.3).
- **Likely reason for the twins** (unverified): the General Index keys documents by a hash of
  the *file*. The same article stored as two PDF files becomes two documents.
  - **Check this first** once slice 0's `docs.tsv` exists: what share of `gi_report.py`'s
    containment ≥0.8 pairs are flagged same-DOI or same-title?
  - If most are, filtering out the same work twice (and collapsing twin clusters before
    ranking) matters more than any choice of bucketing.
- **Groups larger than the cap grow quickly** [measured, `exp_parts.py`]. The share of records
  in groups >20 rises 1.31% → 1.91% → 2.79% → 4.03% → 5.67% from 191k to 3.06M documents, about
  ×1.4 per doubling. Extrapolated, it's about 30% at full scale [est.].
  - **This matters:** a paper-mill family of more than 20 documents (CE-38 is 143) gets *all*
    its shared 5-grams dropped by a hard cap of 20.
  - Pair volume by cap, for the same partitions [measured]: 11M increments at 20, 34M at 100,
    121M at 1,000, 445M at 10,000.
  - Recommendation: raise the cap to 100, weight each shared sample by 1 / log2(group size),
    and handle groups of more than 100 separately (§4, phase D).

## 4. Full-run design (recommendation #1) [est., from the measurements above]

- **Phase A, stream the 16 slices.**
  - Run `gi_sample_5grams.py` at mod-8 (the mod-4 runs already done can be thinned to mod-8
    with `h % 8 == 0`), with 2–4 parallel streams.
  - When each slice finishes, compact it: sort, 48-bit hash, delta-varint, 3-byte local
    document ID. About 3.3 GB per slice, 53 GB total. A slice still in flight needs about
    7 GB raw.
- **Phase B, build posting lists.** Per partition, k-way merge the 16 compacted slices. Drop
  single-document groups and groups over the cap. Keep the groups of 101+ documents in a side
  file for phase D. Records in groups of 2 or more: 24% at 3M documents, growing about
  4–5 points per doubling [measured]. That's about 40–60% at full scale, so 20–30 GB [est.].
- **Phase C, count pairs by document block.**
  - Split documents into 32–64 blocks.
  - For each block, scan the posting lists and emit (x, y) for each x in the block. Count with
    `np.unique` and keep pairs with weighted evidence ≥ T.
  - Volume: about 13B increments at mod-8 [est.], so about 0.2–0.4B per block, 2–3 GB of RAM.
  - Reads: 64 × 25 GB ≈ 1.6 TB of sequential NVMe reads, about 3–6 h, CPU-bound.
  - This replaces `gi_pairs.py`'s global `np.unique`, which won't fit at full scale.
- **Phase D, triage.**
  - Apply the same-work filter (DOI, DOI suffix, title; already in `gi_report.py`).
  - Collapse twin clusters into one representative each.
  - Find families from the >100 groups: documents that keep co-occurring across many large
    groups. That's the population pattern behind CE-38.
  - Rank by weighted evidence, with containment as a secondary column, then hand off to
    `gi_verify.sh`.

**Cost, all 16 slices:**

| | estimate |
|---|---|
| Download | 4.93 TB: about 57 h at 24 MB/s, about 35 h if 4 streams reach about 40 MB/s (the parent saw about 27 MB/s more from extra connections) |
| CPU | Phase A about 0.6 core per stream. Phases B–D a few hours on 4 cores |
| Disk at peak | about 53 (A) + 25 (B) + 14 (two slices in flight) ≈ 90 GB at mod-8. Mod-4 would be about 160 GB, which won't fit beside the existing data |
| RAM | ≤ 8 GB per phase |

## 5. The other ideas evaluated

- **Winnowing without positions.** Winnowing's guarantee of at least one fingerprint per
  window needs positions, and the index has none. Over a bag of n-grams, the analog is
  selecting by content alone, which is what mod-p sampling already is. A per-document
  adaptive rate would break consistency between documents (that's the capped scheme in §1).
- **4-grams, or combining 3-grams.** Not measured. 4-gram rows are about as numerous as 5-gram
  rows (1.68M vs 1.45M in a 100 MB chunk [measured]), and they collide by chance more often.
  Combining 3-grams can't be done, because without positions there's no way to chain them.
  Lemmatized 5-grams already absorb inflection changes. Low priority.
- **Two-stage: signatures first, then re-stream the candidates.** A document's rows are
  scattered across its whole slice file, so re-streaming any candidate costs a full slice
  pass. Not worth it. Verifying from the actual PDFs (`gi_verify.sh`) is the second stage.
- **The keyword table as a bucket key.**
  - **Contents** [measured, `exp_keywords.py`]: YAKE keywords are only 1–3-word phrases (77%
    single words), about 154 per document. 24 GB compressed per slice, about 384 GB total,
    about 4.4 h to download.
  - **Background:** multi-word keyword Jaccard between random document pairs is p99.9 = 0.034,
    max 0.083 (5,000 pairs).
  - **Not measured:** how real overlap pairs score. The chunk held none. The n-gram and keyword
    dumps do share document order, though: 2,925 of 3,285 documents were in both head chunks.
    So a matched test is possible with a different chunk.
  - **Expected use** [est.]: YAKE is deterministic, so near-identical text should give
    near-identical keyword sets. That makes it a cheap screen for twins and whole-document
    republication, and a way to pre-collapse twins. It's useless for partial reuse.
- **Partitioning by journal, field or year from the metadata.** This doesn't reduce download
  or disk, because everything still has to be streamed. It only cuts pair volume, and the cuts
  fall on cross-venue copying, which is the pattern in this project's documented cases. About
  a third of documents also have no metadata to partition on.
- **"The slices are smaller."** They aren't: a slice is about 6.7M documents, roughly 47 times
  the main corpus. Slices are random by hash, so 15/16 of a document's partners sit in other
  slices. Processing one slice at a time finds only 1/16 of the pairs, which is why phase B
  merges across slices by hash partition.

---

# Follow-up: chaining 5-grams, and synonym/vector normalization

## Recommendation, short version

| idea | verdict | before slices 2–15 stream? |
|---|---|---|
| **Tortured-phrase lookup on GI's 2–4-gram rows** | **Do it.** Cheap, and can't be done later without re-downloading | **Yes.** About 15 lines in `gi_sample_5grams.py`, +16% CPU [measured] |
| Rebuilding the order of a document's 5-grams by chaining them | Don't. Fragments come out 1–4 5-grams long; the GI drops too many windows | No |
| Fuzzy matching with 3- or 4-gram keys | Don't. Background noise rises 2–7x and recall falls | No |
| WordNet synonym canonicalization | Don't. Merges only 5 of 27 real tortured substitutions | No |
| Word-vector classes or SimHash | Don't. Noise rises faster than recall | No |

Every second normalized hash costs **2.27x parser CPU** [measured]. That would make the
streams CPU-bound, about 125% of a core against today's 55%, and none of them earned it.

## 6. Method: a GI emulator on real text [measured]

To test any of this I needed text with known word order, and the ability to paraphrase it. The
GI only has bags of n-grams. So:

- **The emulator** (`experiments/gi_emulate.py`) reproduces GI's n-gram rows from raw text with
  spaCy 3 `en_core_web_md`: lemma, lowercase, no punctuation inside, first and last token not a
  stopword. Its 3-grams for the GI sample sentence match the real rows exactly: "important to
  look", "way this technology", "enhance the teaching", "teaching and learning", "learning of
  mathematic".
  - Known differences: GI used spaCy 2, which writes pronouns as "-pron-", and spaCy 3
    lemmatizes "best" as "well".
- **The corpus** is 1,200 real papers from `computer-ethics/library.sqlite3` (read-only),
  1,500–15,000 words each, giving 1,063 5-grams per document.
- **The plants**, 40 of each:
  - **verbatim:** a 400-word passage of one paper inserted into another.
  - **edit:** the same, with each word replaced with probability 0.2, 5% of words deleted and
    5% inserted.
  - **spin30_wn / spin60_wn:** 30% or 60% of content words replaced by a random WordNet
    synonym. This is a spinner model; its output reads like a paper mill's ("dramatise a
    tiered approach", "groundwork their hypnotism").
  - **spin50_vec:** 50% of words replaced by their nearest word-vector neighbour.
  - **doc_spin50:** a whole paper spun at 50% (WordNet), the paper-mill shape.
- **Background** is all 719,400 pairs of real papers, ignoring keys found in more than 10
  documents. 52 real overlap pairs already in the corpus (≥15 shared exact 5-grams: genuine
  reuse or duplicates) are excluded from the background for every scheme, so the threshold
  measures noise, not real duplicates.
- **Recall** means the shared-key count exceeds the background maximum: zero false positives
  among 719k real pairs. This is a 1,200-document world. At 107M documents every threshold
  rises, so compare schemes against each other, not as absolute values.
- **Caveat:** spaCy's worker processes briefly used more than 4 cores during emulation, because
  of BLAS threads. All later runs were pinned.

Full output: `experiments/exp2_results_cap10.txt`.

| keys | background p99.99 / max | verbatim | edit | spin30_wn | spin60_wn | spin50_vec | doc_spin50 |
|---|---|---|---|---|---|---|---|
| **exact 5-gram** | **8 / 14** | **40/40** (median 67 shared) | 18/40 (14) | 39/40 (29) | 10/40 (10) | 5/40 (7) | **40/40** (237) |
| exact 5-gram, mod-8 sample | 1 / 4 | 37/40 (8) | 4/40 (2) | 17/40 (4) | 4/40 (1) | 1/40 (1) | 40/40 (29) |
| exact 4-gram rows | 14 / 35 | 40/40 (79) | 4/40 (23) | 28/40 (41) | 2/40 | 1/40 | 40/40 |
| exact 3-gram rows | 24 / **99** | **12/40** (84) | 1/40 (36) | 0/40 (49) | 2/40 | 1/40 | 40/40 |
| WordNet first-synset 5-gram | 8 / 14 | 40/40 | 19/40 | 39/40 (36) | 23/40 (15) | 5/40 | 40/40 (349) |
| WordNet min-lemma 5-gram | 8 / 14 | 40/40 | 18/40 | 40/40 (39) | **32/40** (21) | 5/40 | 40/40 (415) |
| word-class k=8000 | 9 / 36 | 39/40 | 1/40 | 14/40 | 2/40 | 4/40 (26) | 40/40 |
| word-class k=2000 | 9 / 34 | 39/40 | 1/40 | 18/40 | 2/40 | 6/40 (27) | 40/40 |
| word-class k=500 | 13 / 125 | 4/40 | 0/40 | 0/40 | 2/40 | 1/40 | 36/40 |
| SimHash-24 of 5-gram vector | 61 / 259 | 0/40 | 0/40 | 0/40 | 0/40 | 0/40 | 7/40 |
| SimHash-16 of 5-gram vector | 19 / 87 | 0/40 | 0/40 | 0/40 | 2/40 | 0/40 | 5/40 |

## 7. Chaining 5-grams [measured, emulated documents]

### (a) Rebuilding word order inside a document

The idea: link a 5-gram to another whose first k words are its last k, with k = 4, 3 or 2, so
that a document's bag of 5-grams turns back into ordered runs.

Measured over 300 emulated documents:

- **Most windows are missing.** A document has 18.8 surviving 5-grams per 100 tokens. The
  stopword-ending rule drops most windows.
- **Where the next surviving window starts**, measured from the start of the previous one:

  | offset (words) | share | linkable with |
  |---|---|---|
  | 1 | 27% | k=4 |
  | 2 | 14% | k=3 |
  | 3 | 9.5% | k=2 |
  | 4 | 9.8% | k=1 only |
  | ≥5 | **39%** | nothing: the two windows share no words |

  So no k can link 39% of consecutive surviving windows.
- **Rebuilt runs are short:**
  - Even at k≥2, the median run is **1.33** 5-grams and the 90th percentile 4.3 (about 6–12
    words).
  - At k≥4 it's 1.10 and 2.4. These are fragments, not runs.
- **Ambiguity is not the problem.** The share of 5-grams with two or more possible successors is
  0.6% at k=4, 2.5% at k=3 and 9.7% at k=2. The graph is clear where it exists. It just
  hardly exists: 71–73% of 5-grams have no successor at all.
- **Lemmatization doesn't break chains** within a document, because it's applied consistently.
  The stopword-ending rule is the whole cause.

### Chaining across documents

Measure: the largest chained cluster of the 5-grams two documents share.

| | background max | verbatim | doc_spin50 |
|---|---|---|---|
| largest chained cluster | 13 | 14/40 detected (median cluster 11) | 24/40 |
| plain shared count | 14 | **40/40** | **40/40** |

So chain length is a **weaker** signal than the shared count.

**There's also a structural problem.** Chaining needs consecutive 5-grams. Mod-8 sampling
keeps 1 in 8, so sampled data can't be chained at all. It would need every 5-gram of the
candidate documents, which means a second full pass over the slice (a document's rows are
spread through the whole file).

**Verdict:** not worth building. A runs-based view of the evidence comes from `gi_verify.sh`
running `compare_two_papers.py` on the real PDFs.

### (b) Fuzzy matching with shorter keys

Two 5-grams sharing a 2- or 3-word sub-span is essentially what GI's own 3-gram (or 4-gram)
rows give you, so I tested those rows directly.

- **3-grams:** background max rises from 14 to **99**, and pairs with any shared key rise
  12.6k → 184k. Recall falls on every partial case (verbatim 40 → 12, edit 18 → 1). Real
  passages share more 3-grams (median 84 against 67 5-grams), but stock academic 3-grams grow
  much faster.
- **4-grams:** in between, and still worse than 5-grams on edit (4 vs 18) and spin30 (28 vs 39).
- **The edit case is where the GI approach is weakest.** With every fifth word changed,
  about 14 of 67 5-grams survive, which is 2 samples at mod-8. Nothing tested here recovers it.

## 8. Synonym and vector normalization [measured]

### (i) Does it catch spun copies?

- **On the synthetic WordNet spinner, it looks good.** WordNet min-lemma canonicalization lifts
  spin60 from 10 to 32 of 40, and doc_spin50 from a median of 237 to 415 shared keys, with **no**
  rise in background (still 8 / 14).
- **That test is circular, though.** The spinner and the canonicalizer use the same thesaurus.
- **Against real tortured phrases it fails** [measured, `tortured_wn.txt`]. These are 27 word
  substitutions from the published examples (artificial→counterfeit,
  intelligence→consciousness, deep→profound, random→irregular, network→organization,
  error→blunder, and so on).
  - Only **7 of 27** are even direct WordNet synonyms (share a synset).
  - Min-lemma canonicalization merges 5 of 27, first-synset 2 of 27.
  - Real spinners use other thesauri, pick wrong senses, and make multi-word substitutions
    ("ant colony" → "subterranean insect settlement").
  - Merging words along the synonym graph instead puts 18,994 words (42%) into one class,
    including data, network, model, system and cancer. That makes the background explode.
- **Word-vector classes:**
  - k-means classes over the model's 20,000 word vectors raise background noise (max 14 → 34–36
    at k = 2,000–8,000, and 125 at k=500).
  - They help only against the vector-neighbour spinner (median shared 7 → 27), and even there
    recall at the raised threshold stays 4–6 of 40.
  - They *lower* recall on WordNet-style spins (spin30 39 → 14–18), because the threshold rises.
- **SimHash of averaged 5-gram vectors** is useless: background max 87–259, recall about 0.
- **On determinism:** every scheme tested is deterministic given a pinned model and list
  version. That's not the obstacle; the trade-off between recall and noise is.

### (ii) Detecting tortured phrases directly in GI's rows

This is the effective use [measured, `tortured_gi.txt`]. Tortured phrases are 2–4-word
n-grams, and GI stores every 2–4-gram row, so a known list can be looked up as rows stream past.

- **What I scanned:** 5,627 partial documents in two downloaded chunks, for 28 tortured phrases.
- **What turned up:** "profound learning", "neural organization", "web of thing" and
  "information mining" once each, and "malignant growth" 3 times. Each was in a different
  document.
- **For comparison:** "neural network" was in 85 documents, "deep learning" in 6.
- **A single hit is weak evidence.** "Profound learning" and "malignant growth" can be ordinary
  English, and a chunk holds only part of each document. Cabanac's Problematic Paper Screener
  flags a paper when it has *several* phrases. So the output should be per-document hit counts,
  ranked by how many distinct phrases each document has.
- **At full scale** [est.]: about 1 in 1,100–5,600 documents, so roughly 20k–100k candidate
  documents across 107M, before requiring two or more phrases.

**Change to `gi_sample_5grams.py` before slices 2–15:**
1. Load a phrase list once: the PPS public tortured-phrase list, lemmatized the GI way (spaCy 2
   style), as a set of bytes.
2. In the row loop, before the `p[3] != b"5"` filter: `if p[3] in (b"2", b"3", b"4") and p[2] in
   TORTURED: tort.write(dkey \t phrase \t term_freq)`.

**Cost:**
- **CPU:** +16% parser time (1.32 → 1.52 CPU-seconds per 100 MB) [measured]. That keeps the
  parser at about 0.65 core, so throughput is unchanged.
- **Disk:** kilobytes to megabytes [est.].

**Two caveats:**
- **Slices 0–1 are already streaming without it.** They'd need a re-stream (about 5–6 h each)
  to get it, or can be left out.
- **The list is fixed when each slice streams.** New phrases added to PPS later can't be
  looked up without streaming again.
  - A future-proof alternative is to store hashes of every 2–3-gram row. That's about 4B
    rows per slice [est. from the 100 MB chunk], about 25 GB per slice even compacted. It
    doesn't fit; not recommended.

## Files

Everything under `experiments/`:
- **Scripts:** `inflate.py`, `exp_synthetic.py`, `exp_mod8.py`, `exp_fp.py`, `exp_parts.py`,
  `exp_pair_scaling.py`, `exp_heavy_shape.py`, `exp_twins.py`, `exp_keywords.py`.
- **Outputs:** the matching `*_results.txt` and `*.txt` files.
- **Downloaded chunks:** `ng3_head.zip` (150 MB), `ng0_head.zip` (100 MB) and `kw3_head.zip`
  (30 MB), 280 MB in total. They can be deleted.
- **Follow-up scripts:** `gi_emulate.py`, `exp2_build.py`, `exp2_eval.py`,
  `exp2_tortured_wn.py`, `exp2_tortured_gi.py`.
- **Follow-up outputs:** `exp2_results_cap10.txt`, `tortured_wn.txt`, `tortured_gi.txt`.
- **Follow-up working files:** `corpus.jsonl` (52 MB) and `emulated.pkl` (430 MB, regenerable).
- **Isolated environment:** `.venv/` (spaCy plus `en_core_web_md`, NLTK; 395 MB) and
  `nltk_data/` (WordNet). The project `.venv` was not touched.
