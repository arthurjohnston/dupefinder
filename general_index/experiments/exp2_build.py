"""Build the text-level test set for chaining / normalization experiments and emulate GI rows.

Corpus: 1,200 real papers (computer-ethics library, read-only). Plants (40 each), from donors/hosts
drawn from the first 480 docs:
  verbatim    a 400-word passage of the donor inserted into the host
  edit        same, then every word replaced with p=0.2 by a random corpus word, 5% deleted, 5% inserted
  spin30_wn   passage with 30% of content words replaced by a random WordNet synonym (spinner model)
  spin60_wn   same at 60%
  spin50_vec  passage with 50% of content words replaced by a nearest word-vector neighbour
  doc_spin50  the WHOLE donor spun at 50% (WordNet) as a standalone copy (paper-mill shape)
Writes emulated.pkl: {"orig": [...], "plants": [(type, donor_idx, emulated_doc)], "vocab_nn": {...}}
"""
import json
import os
import pickle
import random
import re

os.environ.setdefault("NLTK_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nltk_data"))
from nltk.corpus import wordnet as wn  # noqa: E402

import numpy as np  # noqa: E402

import gi_emulate  # noqa: E402

rng = random.Random(11)
docs = [json.loads(l)["text"] for l in open("corpus.jsonl")]
nlp = gi_emulate.nlp()
STOP = nlp.Defaults.stop_words
WORD = re.compile(r"[A-Za-z]{3,}")

vocab_words = [w for d in docs[:200] for w in WORD.findall(d)]


def wn_synonym(w):
    lemmas = {l.name().lower() for s in wn.synsets(w.lower()) for l in s.lemmas()}
    lemmas = [l for l in lemmas if l.isalpha() and l != w.lower()]
    return rng.choice(sorted(lemmas)) if lemmas else None


V = nlp.vocab.vectors
_nn_cache = {}


def vec_neighbour(w):
    lw = w.lower()
    if lw in _nn_cache:
        return _nn_cache[lw]
    out = None
    row = V.find(key=nlp.vocab.strings[lw]) if lw in nlp.vocab.strings else -1
    if row is not None and row >= 0:
        keys, _, _ = V.most_similar(V.data[row][None, :], n=15)
        for k in keys[0]:
            s = nlp.vocab.strings[int(k)] if int(k) in nlp.vocab.strings else None
            if s and s.isalpha() and s.lower() != lw and V.find(key=int(k)) != row and s.lower() not in STOP:
                out = s.lower()
                break
    _nn_cache[lw] = out
    return out


def spin(text, p, fn):
    def rep(m):
        w = m.group(0)
        if w.lower() in STOP or rng.random() >= p:
            return w
        s = fn(w)
        return s if s else w
    return WORD.sub(rep, text)


def edit(text):
    out = []
    for w in text.split():
        r = rng.random()
        if r < 0.05:
            continue
        if r < 0.10:
            out.append(rng.choice(vocab_words))
        out.append(rng.choice(vocab_words) if rng.random() < 0.2 else w)
    return " ".join(out)


def passage(text, n=400):
    w = text.split()
    s = rng.randrange(0, max(1, len(w) - n))
    return " ".join(w[s:s + n])


def insert(host, pas):
    paras = host.split("\n\n")
    k = rng.randrange(1, max(2, len(paras)))
    return "\n\n".join(paras[:k] + [pas] + paras[k:])


plants_text = []
idx = list(range(480))
rng.shuffle(idx)
it = iter(idx)
for kind in ("verbatim", "edit", "spin30_wn", "spin60_wn", "spin50_vec", "doc_spin50"):
    for _ in range(40):
        d, h = next(it) if kind != "doc_spin50" else rng.randrange(480), None
        if kind == "doc_spin50":
            plants_text.append((kind, d, spin(docs[d], 0.5, wn_synonym)))
            continue
        h = next(it) if idx else None
        pas = passage(docs[d])
        if kind == "edit":
            pas = edit(pas)
        elif kind == "spin30_wn":
            pas = spin(pas, 0.3, wn_synonym)
        elif kind == "spin60_wn":
            pas = spin(pas, 0.6, wn_synonym)
        elif kind == "spin50_vec":
            pas = spin(pas, 0.5, vec_neighbour)
        plants_text.append((kind, d, insert(docs[h], pas)))
    print(kind, "built", flush=True)

print("example spin60_wn:", spin(passage(docs[0], 40), 0.6, wn_synonym))
print("example spin50_vec:", spin(passage(docs[0], 40), 0.5, vec_neighbour))
em = gi_emulate.emulate(docs + [t for _, _, t in plants_text], n_process=4)
pickle.dump({"orig": em[:len(docs)],
             "plants": [(k, d, e) for (k, d, _), e in zip(plants_text, em[len(docs):])]},
            open("emulated.pkl", "wb"))
print("done", len(em))
