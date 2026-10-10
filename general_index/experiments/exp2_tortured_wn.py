"""Do real tortured-phrase substitutions (Cabanac et al. 2021) collapse under WordNet canonicalization?"""
import os
os.environ.setdefault("NLTK_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nltk_data"))
from nltk.corpus import wordnet as wn
def wn_min(w):
    names = [l.name().lower() for s in wn.synsets(w) for l in s.lemmas()]
    return min(names) if names else w
def wn_first(w):
    ss = wn.synsets(w); return ss[0].lemmas()[0].name().lower() if ss else w
# (original phrase, tortured phrase) -- examples published by Cabanac, Labbe & Magazinov (2021)
PAIRS = [("artificial intelligence", "counterfeit consciousness"), ("deep learning", "profound learning"),
         ("random forest", "irregular woodland"), ("random forest", "arbitrary forest"), ("big data", "colossal information"),
         ("big data", "huge information"), ("neural network", "neural organization"), ("mobile network", "versatile organization"),
         ("wireless network", "remote organization"), ("mean square error", "mean square blunder"),
         ("breast cancer", "bosom peril"), ("kidney failure", "kidney disappointment"), ("signal to noise", "flag to commotion"),
         ("decision tree", "choice tree"), ("cloud computing", "cloud figuring"), ("image processing", "picture handling"),
         ("facial recognition", "facial acknowledgement"), ("ant colony", "subterranean insect settlement"),
         ("naive bayes", "credulous bayes"), ("data warehouse", "information distribution center")]
w_hit = f_hit = tot = 0
for orig, tort in PAIRS:
    a, b = orig.split(), tort.split()
    if len(a) != len(b):
        print(f"  {orig!r} -> {tort!r}: different word count (multi-word substitution)"); continue
    for x, y in zip(a, b):
        if x == y: continue
        tot += 1
        m, f = wn_min(x) == wn_min(y), wn_first(x) == wn_first(y)
        w_hit += m; f_hit += f
        print(f"  {x:>12} -> {y:<14} min-lemma {'SAME' if m else 'diff':4} ({wn_min(x)} / {wn_min(y)})   first-synset {'SAME' if f else 'diff'}")
print(f"\nword substitutions collapsed: min-lemma {w_hit}/{tot}, first-synset {f_hit}/{tot}")
