"""Emulate the General Index's n-gram rows from raw text, keeping positions.

Rules inferred from real GI rows (see BUCKETING_IDEAS.md): spaCy lemma, lowercased; an n-gram is
dropped if any token is punctuation/space or if its first or last token is a stopword; stopwords
are kept mid-phrase. GI used spaCy 2 (pronouns lemmatize to "-pron-"); spaCy 3 here keeps the
pronoun, a small difference.

emulate(docs) -> list of {"tokens": [(lemma, is_stop, is_punct)], "grams": {n: [(start, text)]}}
"""
import spacy

_nlp = None


def nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("en_core_web_md", disable=["parser", "ner"])
        _nlp.max_length = 3_000_000
    return _nlp


def grams_from_tokens(toks, nmax=5):
    out = {n: [] for n in range(1, nmax + 1)}
    L = len(toks)
    for i in range(L):
        for n in range(1, nmax + 1):
            j = i + n
            if j > L:
                break
            span = toks[i:j]
            if any(t[2] for t in span):
                break  # punctuation inside: longer n-grams from i also contain it
            if span[0][1] or span[-1][1]:
                continue
            out[n].append((i, " ".join(t[0] for t in span)))
    return out


def tokens_of(doc):
    return [(t.lemma_.lower().strip(), t.is_stop, t.is_punct or t.is_space or not t.lemma_.strip())
            for t in doc]


def emulate(texts, n_process=4):
    res = []
    for doc in nlp().pipe(texts, n_process=n_process, batch_size=8):
        toks = tokens_of(doc)
        res.append({"tokens": toks, "grams": grams_from_tokens(toks)})
    return res
