# Weighing a case before reporting it (proposal for the code session)

Written 2026-11-01 by the gi-cases agent after prepping GI-877, at the user's request.

**Status (2026-11-01):** proposals 1-3 are done: `.claude/skills/weigh-case/SKILL.md`, step 1b
("A common ancestor") in `.claude/skills/trace-other-sources/SKILL.md`, the "Weigh a case" / "Prep a case"
order in `.claude/agents/gi-cases.md`, and the same condition in `gi_review_cli.py`'s `HEADLESS_PROMPT`.
Proposal 4 (the AI pre-review prompt) is not done yet. Calibration: a first blind run on 16 decided
cases matched 5 of 16; the skill's questions were rewritten from the misses (see the skill's
Calibration section). A held-out run on 16 more matched 7 of 16 (all but one miss one level off),
and the rules were adjusted again from those. It proposes one new skill, one change to an existing skill, one change to the gi-cases
agent definition, and an optional change to the AI pre-review.

## The problem

Nothing in the prep pipeline asks whether a case is worth reporting, or how far. The only severity guidance
is one line in `REVIEW_COMMANDS.md` ("`low` / `medium` / `high` by how much was taken and how central it is
(copied results or data are high)"). `.claude/agents/gi-cases.md` and
`.claude/skills/trace-other-sources/SKILL.md` say nothing about what is normal in a given field. So every
prep does the same full work, whatever the case turns out to be: findings, other-source tracing, the
journal contact, research-integrity contacts for every byline institution, current-institution tracing for
every later author, and the earlier authors.

### GI-877, the worked example

A fixed-point theory pair, both articles in *Fixed Point Theory and Applications* (2013 earlier, 2015
later), different authors, measured 14% / 17%, longest run 44 words. Reading both PDFs showed:

- The shared text is in the Preliminaries (definitions, the function φ: standard material), a few
  Introduction sentences, and the closing steps of the main proof (bounded iterates, then a Cauchy-sequence
  argument through the interior of the fixed-point set). That last part is standard in this literature.
- The later article cites the earlier four times (for the scheme it extends, for an example, and in "our
  results improve…"), just not for the proof steps.
- The later article's main theorem is a different iteration scheme, and its numerical section has no
  counterpart in the earlier article. No data were copied.
- The shared proof argument probably comes from a third work both articles cite (Zegeye, Shahzad and
  Alghamdi, Nonlinear Anal. 74 (2011) 7304-7311). If so, the earlier article isn't the origin either.
- One real slip: an example restated from the earlier article dropped a factor, so it's wrong as printed.
  That's an erratum, not misconduct.

The user asked "for a math paper is this worth reporting?" The honest answer was "low severity, a PubPeer
comment at most, possibly skip". But the prep had already spent most of its effort on contacts for the
institution and author emails, which only a high-severity case gets. One institution's website blocked the
fetcher, and a current-institution trace came to nothing.

## Proposal 1: a new skill, `.claude/skills/weigh-case/SKILL.md`

Run during prep after both PDFs are read and other sources checked, and **before** the contact lookups. Its
output is a recommended severity and reporting route, written under `## Questions` in `findings.md` (never
published), with the reasons. The user still decides. It must not tick findings or write `reviews`.

Questions it works through, in order:

1. **What kind of text was reused?** From weakest to strongest evidence:
   - standard material: definitions, notation, the standard setup, ethics/consent statements, a method
     description that cites its origin;
   - background introduction or literature review;
   - methods, or argument / proof steps;
   - the results, discussion of the results, or the conclusions;
   - results data: tables, counts, figures. This already has the `[data]` tag.
2. **Is the later article's main contribution new?** A new theorem, design or dataset, versus the same
   result reworded (in maths: the old theorem after renaming symbols or substituting parameters, e.g.
   αn → 1 − αn).
3. **Is the earlier article cited, and for what?** Uncited is stronger than cited for something else, which
   is stronger than cited for the reused part.
4. **Is the earlier article the origin?** See proposal 2. A shared ancestor weakens the case against the
   later article, or changes who it's about.
5. **Field norms.** A short section per field, grown as cases come up. Start with:
   - **Mathematics.** Reused preliminaries, standard lemmas and standard proof endings are weak evidence.
     Most editors won't act on them. Strong evidence: theorem statements and whole proofs copied with
     renamed symbols, or a "new" result that reduces to the earlier one. Errors carried over and
     transcription slips are errata. Say when to recommend the reviewer's `e` flag ("outside your
     expertise") instead of judging, e.g. when deciding whether a result is genuinely new needs the
     maths.
   - Other fields can be added the same way: clinical case reports (shared background on a rare
     condition), engineering surveys, etc.
6. **Recommendation:**
   - `none` / not worth reporting;
   - `low`: PubPeer comment, journal email optional;
   - `medium`: PubPeer and the journal;
   - `high`: all four emails.
   One or two sentences on why, and which findings carry the case. This should match the existing rule
   that only high-severity cases get the institution, authors and source-publisher drafts
   (`REVIEW_COMMANDS.md`, "Which emails a case gets follows its severity").

## Proposal 2: a "common ancestor" step in `trace-other-sources`

The skill currently looks only for *additional* sources of the later article. Add a step for the other
risk: the shared passage came from a work that both articles took it from.

- For the longest shared runs, list the references either article cites in or next to the passage, and
  the references both articles cite at all.
- If a common reference is plausibly the origin (a technique or proof that is usually credited to it, or
  wording typical of it), check it as a candidate source the usual way: full text, then measure against
  both articles.
- Record the result in `notes.md`, and in `findings.md` only when verified. When the ancestor is
  confirmed, say so in the weighing (proposal 1, question 4).

## Proposal 3: `.claude/agents/gi-cases.md`, the "Prep a case" contact step follows the weighing

Today, "Prep a case" says to look up the journal, the byline institutions' RIOs, the current
institutions and the earlier authors "without waiting to be asked". Proposed:

- Always: the journal contact (`journal_contacts.json`).
- Only when the weighing recommends **high**, or a finding is tagged `[data]` (which already sends every
  email): the institutions, the current institutions and the earlier authors.
- Otherwise: write in `notes.md` that the lookups were skipped because of the recommendation, so the user
  can ask for them if they disagree.

The review tool's prep prompt (the one that says "Do look up the later authors' institutions' research
integrity officers…") would need the same condition.

## Proposal 4 (optional, code): the AI pre-review

`gi_agent_review.py` suggests `ai_severity`. The same field guidance (proposal 1, questions 1-5), as a
prompt section, would make its suggested severity match what the user decides on cases like GI-877. That
is a prompt and code change, so it's for the code session. Check it against the user's existing `reviews`
verdicts on maths pairs before switching it on.

## Calibration

Before relying on the weighing, run it against cases the user has already decided, including maths pairs
(e.g. those flagged `e`, the `not qualified` query in `REVIEW_COMMANDS.md`), and compare its recommended
severity with the stored `reviews.severity`.
