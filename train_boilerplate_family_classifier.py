#!/usr/bin/env python3
"""Trains a cheap, fast multiclass text classifier that predicts which
*family* of known boilerplate a paragraph belongs to (or "not boilerplate")
-- meant as a generalization layer on top of classify_dupes.py's curated
regex pattern list (TEXT_PATTERNS), not a replacement for it. See the
2026-08-30 conversation this was scoped from: the regex list has perfect
precision on exact matches (each pattern was hand-validated against a real
example before being added) but zero recall on REWORDED variants of the
same known boilerplate family (a Springer "Publisher's note" paragraph
phrased slightly differently than any existing pattern requires). A learned
classifier trained on the regex hits themselves can generalize to those
variants; it cannot discover fundamentally new boilerplate types no pattern
has ever caught (that still needs a human reading a fresh sample, same as
every prior round of this pattern library's growth -- see todo.md's "AI
pre-filter pass").

Why one multiclass model instead of N separate binary ones (the original
ask): functionally equivalent -- a one-vs-rest multiclass classifier IS N
per-family binary detectors under the hood, sharing one TF-IDF vectorizer --
but one artifact is simpler to train, evaluate, and keep in sync than N.

Why TF-IDF + logistic regression, not the sentence-transformer embeddings
already computed elsewhere in this pipeline: the intended deployment point
is embed_paragraphs.py's PRE-embedding boilerplate skip
(skip_boilerplate_paragraphs()), which exists specifically to avoid the cost
of embedding+bucketing paragraphs already known to be junk. Computing a
sentence-transformer embedding just to decide whether to compute a
sentence-transformer embedding defeats that purpose. TF-IDF + logistic
regression trains in seconds on this data volume and costs microseconds per
paragraph at inference, no GPU, no new heavy dependency (scikit-learn is
already present transitively via sentence-transformers, just not yet a
direct requirements.txt entry).

Training data: NOT drawn from potential_dupes (that would only cover
paragraphs that happened to have a similar-embedding partner elsewhere in
the corpus -- a selection bias irrelevant to a classifier meant to run on
every paragraph pre-pairing). Instead, classify_text_patterns_only() --
the exact function skip_boilerplate_paragraphs() already calls -- is run
directly against a large random sample of the `paragraphs` table itself.
Every paragraph it labels becomes a positive example for that label's
family; a matching-sized random sample of paragraphs it does NOT label
becomes the "not_boilerplate" negative class.

Safety framing (why this does NOT get wired into the real pre-embedding
skip path in this script): a paragraph skipped pre-embedding never gets a
second chance at being found as a duplicate (see
classify_text_patterns_only()'s own docstring on this). A learned
classifier's mistakes are categorically more dangerous here than in
classify_dupes.py's post-hoc, human-recoverable candidate classification.
This script only trains and evaluates the model, and validates it in SHADOW
MODE (report what it would flag, change nothing) against the strongest
ground-truth anchors this project has: potential_dupes rows a human already
marked status='confirmed' (real, human-verified duplicate/plagiarism
paragraph pairs -- the one thing this classifier must never flag as
boilerplate). See shadow_validate_against_confirmed(). Wiring this into
embed_paragraphs.py is a deliberate separate step, not automatic, and only
warranted once that validation is clean.
"""

import argparse
import logging
import random
import time
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

import classify_dupes as cd
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("train_boilerplate_family_classifier")

NOT_BOILERPLATE = "not_boilerplate"
OTHER_BOILERPLATE = "other_boilerplate"

# Ordered (family, keywords) -- first keyword match against a TEXT_PATTERNS label wins. Designed by
# reading all 140 distinct fixed labels in classify_dupes.TEXT_PATTERNS (2026-08-30) and grouping by
# what a paragraph in that family actually IS, not by which publisher happens to use it -- the whole
# point of training on families is generalizing across publishers within a family, not per-publisher.
FAMILY_RULES = [
    ("citation_bibliography", ["bibliography", "citation", "reference tag", "reference)", "doi.org",
                                "et al"]),
    ("funding_acknowledgment", ["funding", "grant", "acknowledgment", "acknowledgement", "sponsored by",
                                 "contract"]),
    ("ethics_declarations", ["ethic", "conflict-of-interest", "conflicts of interest", "competing-interest",
                              "declaration of interest", "declaration of generative ai", "consent"]),
    ("copyright_license", ["copyright", "licen", "creative commons", "trademark", "rights-and-permissions",
                            "permission-to-copy", "rights clause", "reuse-permission"]),
    ("repository_deposit", ["repository", "deposit", "take-down", "take down", "tspace", "enlighten",
                             "researchspace", "white rose", "researchonline", "general rights",
                             "document-version", "accepted version"]),
    ("thesis_declaration", ["thesis", "dissertation", "unsw", "sworn-declaration", "candidate as workflow",
                             "candidate's declaration"]),
    ("journal_masthead", ["masthead", "journal homepage", "running header", "page-header/footer",
                           "editorial-office"]),
    ("navigation_chrome", ["table-of-contents", "navigation", "website navigation chrome",
                            "rendering-pipeline artifact"]),
    ("advertising_filler", ["advertising-filler", "jobs board", "symposia registration",
                             "committee roster", "membership advertising", "award nomination",
                             "nomination-announcement"]),
    ("metadata_block", ["orcid", "affiliation/contact", "affiliation block"]),
    ("distribution_imprint", ["distribution boilerplate", "imprint", "address boilerplate",
                               "mission-statement", "mailing-address"]),
]


def family_for_label(label):
    lower = label.lower()
    for family, keywords in FAMILY_RULES:
        if any(kw in lower for kw in keywords):
            return family
    return OTHER_BOILERPLATE


def sample_paragraph_texts(conn, sample_size, seed=0):
    """A random sample of paragraph texts from this corpus, spanning EVERY row regardless of
    `model` -- deliberately including the 'skipped-boilerplate'/'skipped-non-english' sentinel
    rows, not just real-embedding ones. Found live 2026-08-30: embed_paragraphs.py's existing
    pre-embedding skip (skip_boilerplate_paragraphs(), using this exact
    classify_text_patterns_only() function) already removes essentially every regex-matchable
    boilerplate paragraph from the real-embedding pool before this script ever sees it (643,739
    of them, in the main corpus alone) -- so filtering to real-model rows here would sample from
    a pool already purged of the exact positive examples needed to train on. `text` is preserved
    regardless of skip status; only `embedding` is null for those rows.

    Avoids both `SELECT id FROM paragraphs` (a full-table scan just to build a Python-side id
    list -- measured at 127s against the real 9.9M-row main corpus, dominating total runtime
    regardless of --sample-size) and `ORDER BY RANDOM() LIMIT n` (a full-table sort, worse).
    Instead: generate random ids across the table's [MIN(id), MAX(id)] range (a fast query, uses
    the primary key index) and fetch whichever of those ids actually exist and qualify, in
    chunks; oversample the id-candidate pool since ids aren't perfectly dense (some paragraphs
    were deleted/pruned) and retry with a fresh batch of candidates until sample_size is met or
    two consecutive batches yield nothing new (table genuinely has fewer qualifying rows)."""
    min_id, max_id = conn.execute("SELECT MIN(id), MAX(id) FROM paragraphs").fetchone()
    if min_id is None:
        return []
    rng = random.Random(seed)
    texts = []
    seen_ids = set()
    stale_rounds = 0
    while len(texts) < sample_size and stale_rounds < 3:
        remaining = sample_size - len(texts)
        # Oversample 3x this round's target to absorb id gaps / excluded rows in one round-trip.
        candidate_ids = [rng.randint(min_id, max_id) for _ in range(remaining * 3)]
        candidate_ids = [i for i in candidate_ids if i not in seen_ids]
        got_this_round = 0
        for chunk_start in range(0, len(candidate_ids), 900):
            chunk = candidate_ids[chunk_start:chunk_start + 900]
            if not chunk:
                continue
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id, text FROM paragraphs WHERE id IN ({placeholders}) AND text IS NOT NULL",
                chunk,
            ).fetchall()
            for row_id, text in rows:
                seen_ids.add(row_id)
                texts.append(text)
                got_this_round += 1
                if len(texts) >= sample_size:
                    break
            if len(texts) >= sample_size:
                break
        seen_ids.update(candidate_ids)
        stale_rounds = stale_rounds + 1 if got_this_round == 0 else 0
    return texts


def build_dataset(texts):
    """Labels every text with classify_text_patterns_only() -- the SAME function
    skip_boilerplate_paragraphs() already calls -- and buckets the result into a family.
    Returns (X_texts, y_labels)."""
    X, y = [], []
    family_counts = {}
    for text in texts:
        label = cd.classify_text_patterns_only(text)
        family = family_for_label(label) if label else NOT_BOILERPLATE
        X.append(text)
        y.append(family)
        family_counts[family] = family_counts.get(family, 0) + 1
    return X, y, family_counts


def train(X, y, min_examples_per_family=30, class_weight="balanced"):
    """Drops any family with fewer than min_examples_per_family examples (not enough signal to
    learn a real decision boundary -- those stay exactly as they are today, regex-only, which is
    already perfect precision on what they match). Returns (vectorizer, model, kept_families,
    dropped_families)."""
    from collections import Counter
    counts = Counter(y)
    kept_families = {f for f, n in counts.items() if n >= min_examples_per_family or f == NOT_BOILERPLATE}
    dropped_families = {f: n for f, n in counts.items() if f not in kept_families}

    X_kept, y_kept = zip(*[(x, label) for x, label in zip(X, y) if label in kept_families])

    vectorizer = TfidfVectorizer(max_features=20_000, ngram_range=(1, 2), min_df=2, sublinear_tf=True)
    X_vec = vectorizer.fit_transform(X_kept)

    model = LogisticRegression(max_iter=2000, class_weight=class_weight)

    X_train, X_test, y_train, y_test = train_test_split(
        X_vec, y_kept, test_size=0.2, random_state=0, stratify=y_kept
    )
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    report = classification_report(y_test, y_pred, zero_division=0)

    # Refit on the full kept dataset for the artifact we actually save/deploy.
    model_full = LogisticRegression(max_iter=2000, class_weight=class_weight)
    model_full.fit(X_vec, y_kept)

    return vectorizer, model_full, kept_families, dropped_families, report, (X_test, y_test)


def evaluate_gated_decision(vectorizer, model, X_test, y_test, min_confidence):
    """The report from train() evaluates raw argmax prediction -- NOT the actual decision
    predict_families() would make, which additionally requires min_confidence before trusting a
    boilerplate prediction over not_boilerplate. This is the metric that actually matters for
    safety: of paragraphs truly NOT boilerplate, what fraction would the real gated decision
    wrongly flag as some boilerplate family (and so, if ever wired into the real pre-embedding
    skip, wrongly and irreversibly exclude from candidate generation)? Reports that false-flag
    rate plus, per family, what fraction of true positives the gate still catches (recall drops
    somewhat vs. raw argmax, trading recall for the safety margin)."""
    # X_test is already TF-IDF-transformed (from train_test_split on X_vec) -- predict_families()
    # normally takes raw text and vectorizes internally, so call the pieces directly here instead.
    proba = model.predict_proba(X_test)
    classes = model.classes_
    y_pred_gated = []
    for row in proba:
        top_idx = row.argmax()
        top_family, top_conf = classes[top_idx], row[top_idx]
        y_pred_gated.append(NOT_BOILERPLATE if (top_family != NOT_BOILERPLATE and top_conf < min_confidence)
                             else top_family)

    not_bp_total = sum(1 for y in y_test if y == NOT_BOILERPLATE)
    not_bp_wrongly_flagged = sum(1 for y, p in zip(y_test, y_pred_gated)
                                  if y == NOT_BOILERPLATE and p != NOT_BOILERPLATE)
    false_flag_rate = not_bp_wrongly_flagged / not_bp_total if not_bp_total else 0.0

    report = classification_report(y_test, y_pred_gated, zero_division=0)
    return false_flag_rate, not_bp_wrongly_flagged, not_bp_total, report


def predict_families(vectorizer, model, texts, min_confidence=0.5):
    """Returns a list of (predicted_family, confidence) -- predicted_family is NOT_BOILERPLATE
    if the top prediction's probability is below min_confidence (an unconfident guess should not
    count as a skip decision)."""
    X_vec = vectorizer.transform(texts)
    proba = model.predict_proba(X_vec)
    classes = model.classes_
    results = []
    for row in proba:
        top_idx = row.argmax()
        top_family, top_conf = classes[top_idx], row[top_idx]
        if top_family != NOT_BOILERPLATE and top_conf < min_confidence:
            results.append((NOT_BOILERPLATE, top_conf))
        else:
            results.append((top_family, top_conf))
    return results


def shadow_validate_against_confirmed(conn, vectorizer, model, min_confidence=0.5, logger_=None):
    """The critical safety gate: pulls every paragraph text involved in a status='confirmed'
    potential_dupes row (a human already verified this is a REAL duplicate/plagiarism match) and
    checks whether the trained classifier would flag either side as boilerplate. Any hit here is
    disqualifying -- this script does not wire the model into the real pipeline if this is
    non-empty. Read-only; changes nothing."""
    rows = conn.execute(
        """SELECT DISTINCT pr.id, pr.text, pd.id FROM paragraphs pr
           JOIN potential_dupes pd ON pr.id IN (pd.paragraph_id_1, pd.paragraph_id_2)
           WHERE pd.status = 'confirmed'"""
    ).fetchall()
    if not rows:
        if logger_:
            logger_.info("shadow validation: 0 status='confirmed' rows in this corpus -- nothing to check")
        return []
    texts = [r[1] for r in rows]
    predictions = predict_families(vectorizer, model, texts, min_confidence=min_confidence)
    failures = []
    for (para_id, text, pd_id), (family, conf) in zip(rows, predictions):
        if family != NOT_BOILERPLATE:
            failures.append((para_id, pd_id, family, conf, text))
            if logger_:
                logger_.error("SHADOW VALIDATION FAILURE: paragraph id=%d (potential_dupes id=%d, "
                               "CONFIRMED real match) would be flagged as %r (confidence %.2f): %r",
                               para_id, pd_id, family, conf, text[:150])
    if logger_:
        logger_.info("shadow validation: checked %d paragraph(s) from confirmed matches, %d failure(s)",
                      len(rows), len(failures))
    return failures


def shadow_validate_against_backtest_suite(vectorizer, model, min_confidence=0.5, work_dir=None, logger_=None):
    """A second, independent ground-truth check alongside shadow_validate_against_confirmed():
    every paragraph in tests/cases/*.json's documented real plagiarism cases (plus the negative
    control), via whatever isolated per-case library.sqlite3 databases tests/run_tests.py
    --keep-work already produced under tests/work/<case-name>/. Unlike the confirmed-status
    check (this project's own accumulated review history), these are external, independently
    curated cases -- real arXiv withdrawals, a RetractionWatch-documented plagiarism case -- so a
    clean result here is evidence the classifier generalizes, not just that it agrees with itself.

    Does NOT run tests/run_tests.py itself (that needs --email and, for cases without a local
    fixture, the network) -- run it with --keep-work first; this function only reads whatever
    tests/work/ already contains. Silently returns 0 checked if that directory doesn't exist or
    is empty, same "nothing to check" framing as the confirmed-status function above."""
    work_dir = Path(work_dir) if work_dir else Path(__file__).resolve().parent / "tests" / "work"
    if not work_dir.exists():
        if logger_:
            logger_.info("shadow validation (back-test suite): %s doesn't exist -- run "
                          "tests/run_tests.py --keep-work first if you want this check", work_dir)
        return []

    failures = []
    total_checked = 0
    for case_dir in sorted(work_dir.iterdir()):
        db_path = case_dir / "library.sqlite3"
        if not db_path.exists():
            continue
        conn = db.connect(db_path)
        rows = conn.execute("SELECT id, text FROM paragraphs WHERE embedding IS NOT NULL").fetchall()
        conn.close()
        if not rows:
            continue
        texts = [r[1] for r in rows]
        predictions = predict_families(vectorizer, model, texts, min_confidence=min_confidence)
        for (para_id, text), (family, conf) in zip(rows, predictions):
            total_checked += 1
            if family != NOT_BOILERPLATE:
                failures.append((case_dir.name, para_id, family, conf, text))
                if logger_:
                    logger_.warning("back-test suite: case=%s paragraph id=%d flagged as %r "
                                     "(confidence %.2f): %r -- verify by hand whether this is a "
                                     "real false-positive or a legitimate citation/boilerplate "
                                     "paragraph that happens to sit in a positive case's PDF",
                                     case_dir.name, para_id, family, conf, text[:150])
    if logger_:
        logger_.info("shadow validation (back-test suite): checked %d paragraph(s) across %d case(s), "
                      "%d flagged", total_checked, len(list(work_dir.iterdir())), len(failures))
    return failures


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    parser.add_argument("--sample-size", type=int, default=400_000)
    parser.add_argument("--min-examples-per-family", type=int, default=30)
    parser.add_argument("--min-confidence", type=float, default=0.9,
                         help="default 0.9 -- validated 2026-08-30: at the looser default of 0.5 "
                              "combined with class_weight='balanced', 8.08%% of true non-boilerplate "
                              "paragraphs got wrongly flagged (measured on the held-out test set); "
                              "0.9 combined with --class-weight none brought that down to 0.10%%")
    parser.add_argument("--class-weight", default="none",
                         help="sklearn LogisticRegression class_weight -- 'none' (default, validated "
                              "2026-08-30) prioritizes the not_boilerplate safety number; 'balanced' "
                              "trades that away for much better minority-family recall (see "
                              "--min-confidence's own help for the measured tradeoff) -- only use "
                              "'balanced' if generalizing to more boilerplate variants matters more "
                              "than this being safe to ever wire into a pre-embedding skip")
    parser.add_argument("--out", type=Path, default=Path("boilerplate_family_classifier.joblib"))
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)

    logger.info("sampling up to %d paragraph(s) from %s...", args.sample_size, args.library_db)
    t0 = time.time()
    texts = sample_paragraph_texts(conn, args.sample_size, seed=args.seed)
    logger.info("sampled %d paragraph(s) in %.1fs", len(texts), time.time() - t0)

    logger.info("labeling via classify_text_patterns_only()...")
    t0 = time.time()
    X, y, family_counts = build_dataset(texts)
    logger.info("labeled in %.1fs -- family distribution:", time.time() - t0)
    for family, n in sorted(family_counts.items(), key=lambda kv: -kv[1]):
        logger.info("  %8d  %s", n, family)

    logger.info("training...")
    t0 = time.time()
    class_weight = None if args.class_weight.lower() == "none" else args.class_weight
    vectorizer, model, kept_families, dropped_families, report, (X_test, y_test) = train(
        X, y, min_examples_per_family=args.min_examples_per_family, class_weight=class_weight
    )
    logger.info("trained in %.1fs on families: %s", time.time() - t0, sorted(kept_families))
    if dropped_families:
        logger.info("dropped %d family(-ies) below --min-examples-per-family=%d (stay regex-only): %s",
                     len(dropped_families), args.min_examples_per_family, dropped_families)
    logger.info("held-out test set report (raw argmax, NOT the real gated decision):\n%s", report)

    false_flag_rate, n_wrong, n_total, gated_report = evaluate_gated_decision(
        vectorizer, model, X_test, y_test, args.min_confidence
    )
    logger.info("held-out test set report (REAL gated decision, min_confidence=%.2f):\n%s",
                 args.min_confidence, gated_report)
    logger.info("*** safety number: %d/%d (%.2f%%) of true non-boilerplate paragraphs would be "
                "WRONGLY flagged as boilerplate by the real gated decision ***",
                n_wrong, n_total, false_flag_rate * 100)

    failures = shadow_validate_against_confirmed(conn, vectorizer, model,
                                                   min_confidence=args.min_confidence, logger_=logger)
    backtest_failures = shadow_validate_against_backtest_suite(
        vectorizer, model, min_confidence=args.min_confidence, logger_=logger
    )

    joblib.dump({"vectorizer": vectorizer, "model": model, "min_confidence": args.min_confidence,
                 "kept_families": sorted(kept_families)}, args.out)
    logger.info("saved model to %s", args.out)

    if failures:
        logger.error("%d shadow-validation failure(s) against CONFIRMED real matches -- "
                      "DO NOT wire this model into the real pre-embedding skip path", len(failures))
    else:
        logger.info("shadow validation clean against every status='confirmed' row in this corpus")
    if backtest_failures:
        logger.warning("%d paragraph(s) flagged in the back-test suite -- inspect by hand "
                        "(a flagged citation string inside a positive case's PDF is expected and "
                        "fine; a flagged paragraph that's part of the actual matched plagiarism "
                        "passage is not)", len(backtest_failures))


if __name__ == "__main__":
    main()
