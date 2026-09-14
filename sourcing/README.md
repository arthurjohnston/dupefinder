# Computer-Ethics Coverage Estimator

Estimate how much **computer-ethics literature** is represented in a local
Sci-Hub DOI/metadata index.

The program does **not download papers**. It only works with bibliographic
metadata and DOI identifiers.

## What it measures

The program defines a target corpus operationally:

1. Search OpenAlex for a set of computer-ethics-related queries.
2. Score the returned works with an inspectable keyword heuristic.
3. Keep works that have a DOI and pass the relevance threshold.
4. Normalize a local Sci-Hub DOI list.
5. Compute exact DOI overlap.

The main result is:

```text
coverage = candidate computer-ethics DOIs present in Sci-Hub index
           --------------------------------------------------------
                candidate computer-ethics DOIs from OpenAlex
```

This is much more efficient than trying to look up millions of Sci-Hub records
against OpenAlex.

## Requirements

- Python 3.10+
- `requests`
- A local file containing Sci-Hub DOI metadata or DOI identifiers
- Recommended: a free OpenAlex API key

As of 2026, OpenAlex uses API keys for meaningful API usage. You can create a
free key in your OpenAlex account settings. The script also works without one
for small/test queries, subject to OpenAlex's current limits.

## 1. Set up Python

```bash
cd computer_ethics_coverage

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## 2. Get a DOI index for the local Sci-Hub metadata you already have

You need a **metadata/index file**, not the PDFs themselves.

The script accepts:

- plain text containing DOIs anywhere in each line
- `.csv`
- `.tsv`

Examples:

```text
10.1145/1234567.1234568
10.1007/s00146-020-01033-8
https://doi.org/10.1177/20539517211053713
```

For CSV/TSV, either let the program scan all fields or name the DOI column:

```bash
python estimate_coverage.py extract \
  --input /path/to/scihub_metadata.csv \
  --column doi \
  --output output/scihub-dois.txt
```

If your file is already one DOI per line, you can skip `extract`.

### Important limitation

A BitTorrent `.torrent` file by itself generally describes files/pieces. It
does not necessarily contain DOI metadata. You need an accompanying index or
database that maps the archive's papers to DOI identifiers.

If your Sci-Hub metadata is in SQLite, SQL, or another database format, export
the DOI column first, for example:

```bash
sqlite3 metadata.db 'SELECT doi FROM papers WHERE doi IS NOT NULL;' \
  > output/scihub-dois.txt
```

Adjust the table/column names to match your database.

## 3. Get an OpenAlex API key

Create an OpenAlex account and copy the API key from the account settings.

Then:

```bash
export OPENALEX_API_KEY='YOUR_KEY_HERE'
```

Do not commit the key to source control.

## 4. Review the search queries

The default query set is in:

```text
queries.txt
```

It includes phrases such as:

- computer ethics
- information ethics
- AI ethics
- algorithmic fairness
- responsible AI
- data ethics
- privacy ethics
- surveillance ethics
- software engineering ethics

Edit this file to broaden or narrow your definition of the field.

This matters more than any statistical calculation: "computer ethics" has no
single universally defined bibliographic boundary.

## 5. Build the candidate corpus

Run:

```bash
python estimate_coverage.py collect
```

By default, this fetches at most 5,000 raw OpenAlex results **per query** and
keeps candidates with relevance score >= 4.

Output:

```text
output/candidates.jsonl
```

Each record contains roughly:

```json
{
  "doi": "10.1234/example",
  "openalex_id": "https://openalex.org/W...",
  "title": "Example title",
  "year": 2024,
  "cited_by_count": 17,
  "relevance_score": 8,
  "relevance_reasons": ["strong:ai ethics", "strong-title"],
  "matched_queries": ["AI ethics"]
}
```

### Fetch more results

OpenAlex supports cursor pagination. To remove this program's per-query cap:

```bash
python estimate_coverage.py collect --max-per-query 0
```

That can issue substantially more requests.

A more conservative run:

```bash
python estimate_coverage.py collect \
  --max-per-query 1000 \
  --min-score 5
```

### Response caching (avoid re-paying for repeat queries)

Every raw OpenAlex page response is cached to disk under `output/openalex_cache/`
(one subfolder per query, one file per page, in cursor order). Re-running
`collect` — including with a different `--min-score` — replays cached pages
instead of re-querying the API, and only fetches pages beyond what's already
cached. That also means raising `--max-per-query` on a later run resumes each
query from where its cache left off rather than re-fetching it from page one.

```bash
# Uses/extends output/openalex_cache/ automatically -- no extra flags needed.
python estimate_coverage.py collect --max-per-query 2000 --min-score 4

# Point at a different cache location:
python estimate_coverage.py collect --cache-dir /path/to/cache

# Bypass the cache entirely for this run:
python estimate_coverage.py collect --no-cache

# Ignore and overwrite existing cached pages (e.g. queries.txt changed
# enough that you want fresher OpenAlex results for the same query text):
python estimate_coverage.py collect --refresh-cache
```

Since `queries.txt` and `--min-score`/`--max-per-query` are already recorded
under Reproducibility below, keep `output/openalex_cache/` around (or back it
up) alongside those if you want to reproduce a run without re-querying at all.

## 6. Estimate overlap

If the DOI list is plain text:

```bash
python estimate_coverage.py estimate \
  --scihub-dois output/scihub-dois.txt
```

Or directly from CSV:

```bash
python estimate_coverage.py estimate \
  --scihub-dois /path/to/scihub_metadata.csv \
  --column doi
```

Example console output:

```text
Computer-ethics DOI coverage
----------------------------
Candidate works with DOI: 8,412
Present in local Sci-Hub DOI index: 5,963
Coverage: 70.89%
Descriptive Wilson 95% interval: 69.91%-71.85%
```

The actual numbers will depend on your local index, query set, relevance
threshold, and the date of the OpenAlex search.

## Outputs

### `output/coverage_report.json`

Machine-readable aggregate results, including:

- number of target works
- number present
- percentage covered
- coverage by publication year
- coverage by relevance score

### `output/candidate_coverage.csv`

One row per candidate paper, including:

- DOI
- whether it occurs in the local Sci-Hub DOI index
- title
- year
- citation count
- relevance score
- matched search queries

This file is useful for manually inspecting false positives.

## How relevance scoring works

The classifier is intentionally simple and auditable.

It gives points for:

- explicit phrases such as `computer ethics` or `algorithmic fairness`
- ethics-related language
- computing-related language
- strong evidence in the title

It penalizes some obvious unrelated ethics categories when no strong computing
phrase is present.

Default threshold:

```text
score >= 4
```

For higher precision:

```bash
python estimate_coverage.py collect --min-score 6
```

For higher recall:

```bash
python estimate_coverage.py collect --min-score 3
```

A useful workflow is to run thresholds 4, 5, and 6 and see whether the coverage
percentage is stable.

## Better validation

The largest source of uncertainty is **field definition/classification**, not
the DOI intersection.

For a more defensible estimate:

1. Randomly inspect 100-200 rows from `candidate_coverage.csv`.
2. Label each as relevant/not relevant.
3. Tune `queries.txt` and `--min-score`.
4. Run again.
5. Report coverage at multiple thresholds.

For example:

```text
threshold >= 4: 72%
threshold >= 5: 74%
threshold >= 6: 75%
```

If the estimate barely moves, the conclusion is less sensitive to the
classifier.

## Performance

The Sci-Hub DOI file is streamed while reading, but unique DOIs are stored in a
Python `set` for fast membership testing.

Roughly speaking, many millions of DOIs may require hundreds of MB to a few GB
of RAM depending on DOI lengths and Python overhead.

If your index is too large for RAM, the next step would be one of:

- SQLite with an indexed DOI column
- DuckDB
- a sorted DOI file + merge join
- a Bloom filter followed by exact verification

For most desktop machines, a DOI list in the low millions is straightforward.

## Reproducibility

Save these together:

```text
queries.txt
output/candidates.jsonl
output/coverage_report.json
output/candidate_coverage.csv
```

Also record:

- date of the OpenAlex collection
- `--min-score`
- `--max-per-query`
- origin/date of the Sci-Hub DOI metadata index

OpenAlex changes over time as its catalog is updated.

## Caveats

### 1. DOI-only analysis misses non-DOI literature

Some older books, conference material, essays, and humanities literature have
no DOI. They will not be counted.

### 2. OpenAlex search is not a gold-standard field taxonomy

The candidate corpus is based on metadata/text search and heuristic
classification.

### 3. A DOI in the local index is not proof that a current download works

This script measures representation in the supplied metadata/index. It does not
test file availability or retrieve content.

### 4. The Wilson interval is descriptive here

The program emits a Wilson interval around the observed fraction, but the
candidate set is not a simple random sample of all computer-ethics scholarship.
Do not interpret the interval as capturing classification or OpenAlex coverage
bias.

## Suggested first run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export OPENALEX_API_KEY='YOUR_KEY'

python estimate_coverage.py collect \
  --max-per-query 2000 \
  --min-score 4

python estimate_coverage.py estimate \
  --scihub-dois /path/to/your/scihub-dois.txt
```

Then open:

```text
output/candidate_coverage.csv
```

and manually inspect a sample of the papers with relevance scores 4-5. That is
the fastest way to determine whether the search definition is too broad.

## Select only the required SciMag ZIPs

The historical SciMag torrents use a two-level packing scheme:

```text
1 torrent = 100,000 SciMag IDs
1 torrent ~= 100 ZIP files
1 ZIP = 1,000 SciMag IDs
PDF path inside ZIP ~= <DOI>.pdf
```

For example, SciMag ID `27,345,678` maps to:

```text
sm_27300000-27399999.torrent
libgen.scimag27345000-27345999.zip
```

That means BitTorrent can normally select the required **1,000-ID ZIP**, not
the single paper inside it.

### 1. Export the matched SciMag rows

Once your candidate DOI list is loaded into a MariaDB table named
`candidate_dois`, export the matching SciMag IDs and DOIs:

```bash
mariadb --batch --raw scimag \
  --execute="
    SELECT s.ID, s.DOI
    FROM scimag AS s
    JOIN candidate_dois AS c
      ON s.DOI = c.doi;
  " \
  | tr '\t' ',' \
  > output/scimag_matches.csv
```

The CSV needs a header:

```text
ID,DOI
```

### 2. Generate the client-independent manifest

```bash
python torrent_manifest.py build \
  --scimag-csv output/scimag_matches.csv
```

Outputs:

```text
output/torrents/torrent_manifest.csv
output/torrents/wanted_zips/
```

The main CSV maps each paper:

```text
DOI
 -> SciMag ID
 -> 100,000-ID torrent
 -> 1,000-ID ZIP
 -> expected DOI.pdf path inside the ZIP
```

Each file under `wanted_zips/` lists the ZIPs required from one torrent.

### 3. Generate exact torrent file indexes

Clone/download the `.torrent` metadata repository, then point the script at the
directory containing `sm_*.torrent`:

```bash
python torrent_manifest.py build \
  --scimag-csv output/scimag_matches.csv \
  --torrent-dir ~/Downloads/sci-hub-torrents/sci-hub-torrent
```

The script parses the actual `.torrent` metadata and resolves the ZIPs to exact
BitTorrent file indexes. It also generates:

```text
output/torrents/aria2_selective_commands.sh
```

Conceptually:

```bash
aria2c --select-file=12,37,88 /path/to/sm_27300000-27399999.torrent
```

The indexes are derived from the actual torrent metadata, not guessed.

### 4. Inspect any torrent manually

```bash
python torrent_manifest.py inspect \
  ~/Downloads/sci-hub-torrents/sci-hub-torrent/sm_27300000-27399999.torrent
```

This prints:

```text
file-index    size    internal/path
```

Use `--limit 0` to show every entry.

### Other torrent clients

`wanted_zips/*.wanted.txt` is the generic output. When `.torrent` metadata is
supplied, each line also has the exact BitTorrent file index and internal path.

- **aria2c**: generated shell commands are ready to use.
- **Transmission**: add the torrent paused, then mark only those file indexes
  as wanted.
- **qBittorrent**: search/select the listed ZIP paths, or use the file indexes
  with its Web API.
- **GUI clients**: use the ZIP basenames from `wanted_zips`.

The minimum practical download granularity for this archive is therefore one
ZIP of roughly 1,000 SciMag records. If several desired papers land in the same
ZIP, that overhead is shared.
