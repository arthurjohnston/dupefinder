#!/usr/bin/env python3
"""Automated first pass at potential_dupes.ai_check -- the programmatic half
of the manual classification pass documented in todo.md's "AI pre-filter
pass" section. Applies the fixed pattern library that pass identified
(publisher/venue boilerplate, shared bibliography entries, standard
methodology templates, quoted external legal/rights text, author-metadata
blocks, sequential report/dataset editions, and the numeric/tabular
embedding-false-positive check) to every same_paper=0 candidate that still
has ai_check IS NULL, so re-running build_dupe_candidates.py and picking up
new candidates doesn't mean redoing that whole pass by hand again.

What this does NOT do: replicate the ~150 individual hand-reviewed verdicts
from the original pass (a specific bibliography entry that slipped past a
pattern, a specific same-author prose overlap mixed into an otherwise
citation-heavy pair, etc.) -- those were genuine one-off judgment calls on
specific paragraph pairs, not reproducible rules. One durable rule from that
pass *is* encoded here: a candidate that survives every "no" pattern AND is
same_author=1 gets marked 'yes' (self-reuse) automatically, since 198 of the
201 hand-reviewed 'yes' verdicts in the original pass were exactly this
(see todo.md) -- same_author=0 survivors are left NULL, since those are
exactly the rare, high-value cases (only 3 of 201) that deserve a real look,
by review_dupes.py or a human/AI re-run of this same kind of pass.

Safe to run repeatedly (only ever touches rows where ai_check IS NULL, same
as build_dupe_candidates.py never overwriting a human's status/reviewed_at).
"""

import argparse
import logging
import re
import time
from pathlib import Path

import db
import text_overlap as to

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("classify_dupes")

# (pattern, label) -- checked against either paragraph's text; first match wins.
# See todo.md's "AI pre-filter pass" section for how each of these was found.
TEXT_PATTERNS = [
    (r"creative commons (license )?attribution", "CC license boilerplate"),
    (r"this work is licensed under", "CC license boilerplate"),
    (r"copyright (held by|for this paper) (the )?owner", "publisher copyright boilerplate"),
    (r"publication rights licensed to acm", "ACM copyright boilerplate"),
    (r"permission to make digital or hard copies", "ACM permission-to-copy boilerplate"),
    (r"declaration of competing interest", "competing-interest disclaimer boilerplate"),
    (r"competing interests? the authors declare", "competing-interest disclaimer boilerplate"),
    (r"conflicts? of interest", "conflict-of-interest disclaimer boilerplate"),
    (r"declaration of conflicting interest", "conflict-of-interest disclaimer boilerplate"),
    (r"no (potential )?conflicts? of interest", "conflict-of-interest disclaimer boilerplate"),
    (r"ceur workshop proceedings", "CEUR proceedings boilerplate"),
    (r"^arxiv:\d{4}\.\d{4,5}v\d", "bare arXiv identifier header"),
    (r"manuscript submitted to acm", "ACM manuscript-submission header"),
    (r"views and opinions expressed are", "company/employer disclaimer boilerplate"),
    (r"disclaimer this paper was prepared", "company/employer disclaimer boilerplate"),
    (r"not a product of the research", "company/employer disclaimer boilerplate"),
    (r"table of contents", "table-of-contents navigation text"),
    (r"\.{5,}\s*\d+", "table-of-contents dot-leader navigation text"),
    (r"ccs concepts", "ACM CCS taxonomy tag boilerplate"),
    (r"acknowledg(e)?ments?\b.{0,150}(grant|fund|supported by|research council|foundation)",
     "funding acknowledgment (grant-specific) boilerplate"),
    (r"this material is based upon work supported by the national science foundation under grant",
     "NSF grant funding-acknowledgment boilerplate"),
    (r"doi\.org/10\.|doi:\s*10\.", "shared bibliography/citation entry (external DOI)"),
    (r"^\[\d+\]", "shared bibliography/citation entry (numbered reference)"),
    (r"<jrn>", "shared bibliography/citation entry (structured reference tag)"),
    (r"^.{0,70}\bet al\.?[,.]?\s*\(?(19|20)\d{2}\)?",
     "shared bibliography/citation entry (author-year, near start of text)"),
    (r"european convention on human rights|gdpr anti-pattern|article \d+[:.]\s*(lawfulness|right to|freedom)",
     "shared quotation of external legal/rights text (ECHR/GDPR article text), not original prose"),
    (r"sumak kawsay|pachamama|african commission on human and peoples",
     "shared quotation of external rights-declaration/indigenous-rights source text"),
    (r"page number not for citation purposes", "journal page-header/footer template (JMIR-style running header)"),
    (r"i felt very confident using|i needed to learn a lot of things before|"
     r"i found the various functions in this|i thought there was too much inconsistency|"
     r"i found the system unnecessarily complex|i found the system very cumbersome|"
     r"i imagine that most people would learn to use this|"
     r"i found the .{0,40} very (awkward|cumbersome) to use",
     "standard System Usability Scale (SUS) survey template"),
    (r"specify the methods used to decide whether a study met the inclusion criteria|"
     r"describe any methods used to explore possible causes of heterogeneity|"
     r"briefly summarise the characteristics and risk of bias among",
     "standard PRISMA systematic-review reporting checklist template"),
    (r"was the .raw. data saved|does the dataset identify any subpopulations|"
     r"over what timeframe was the data collected|please provide a link or other access point",
     "standard 'Datasheets for Datasets' documentation template (Gebru et al. 2018)"),
    (r"sustainable development goals \(sdgs\) at a un summit", "standard SDG framing boilerplate"),
    (r"orcid\(s\)|orcid\.org/\d{4}", "author affiliation/ORCID metadata block, not content"),
    (r"case studies within the ai ethics and governance in practice workbook series",
     "workbook-series boilerplate statement (same publisher series intro)"),
    (r"^\s*(disclaimer|copyright ©|all rights reserved)", "publisher/series disclaimer boilerplate"),
    (r"association for the advancement of artificial intelligence|all rights reserved",
     "publisher copyright/rights boilerplate"),
    # Added 2026-08-24 (todo.md post-mortem 7): found by hand-reading a 100-row sample of
    # unclassified cross-author candidates that the pattern list above missed entirely --
    # categories the earlier "AI pre-filter pass" never saw, not a re-check of existing ones.
    (r"journal homepage", "journal masthead boilerplate (homepage link)"),
    (r"publisher.s note\b.{0,40}all claims expressed", "Springer/Frontiers 'Publisher's note' boilerplate"),
    (r"national technical information service", "NTIS government report distribution boilerplate"),
    (r"back close\s+full screen\s*/\s*esc|printer-friendly version",
     "Copernicus/EGU discussion-paper website navigation chrome (scraped UI text, not article content)"),
    (r"please cite this (paper|article) as", "citation self-reference boilerplate"),
    (r"electronic supplementary material", "electronic-supplementary-material notice boilerplate"),
    (r"^\d{1,3}\.\s+[A-ZА-Я][a-zа-я]+,?\s+[A-ZА-Я]{1,3}\.",
     "numbered bibliography entry (Lastname, Initial. or Lastname Initial. style -- comma is optional; "
     "requires initials immediately followed by a period so an ordinary numbered section heading like "
     "'3. Results We found...' doesn't misfire)"),
    # Added 2026-08-24 (todo.md post-mortem 7 continued -- rank 101-250 of the same ranked-by-lcs_ratio
    # sample, read after the first 100 turned up more categories the first pass of new patterns hadn't
    # covered yet).
    (r"juris\s*dictional claims in published maps",
     "'Publisher's note...jurisdictional claims in published maps and institutional affiliations' "
     "boilerplate (distinct wording from the Frontiers 'Publisher's note...all claims expressed' pattern "
     "above -- both are Publisher's Note variants, used near-verbatim by Springer, MDPI, and others; "
     "allows for the 'juris dictional' pdftotext dehyphenation-artifact spacing seen on some PDFs, and "
     "matches on the 'jurisdictional claims...' clause itself rather than requiring the 'remains neutral "
     "with regard to' lead-in, since some paragraph splits cut that lead-in off)"),
    (r"eprints\.gla\.ac\.uk|enlighten.{0,15}research publications by members of the university of glasgow",
     "University of Glasgow 'Enlighten' institutional-repository deposit stamp"),
    (r"performed under the auspices of the u\.?s\.?\s*department of energy",
     "DOE national-laboratory funding-acknowledgment boilerplate"),
    (r"office of scientific and technical information|reproduced directly from the best available copy",
     "DOE/OSTI report distribution boilerplate"),
    (r"superintendent of documents", "U.S. Government Printing Office distribution boilerplate"),
    (r"cru[ei]-c(are|sic) agreement", "Italian/Spanish national open-access funding agreement boilerplate"),
    # Added 2026-08-24 (todo.md post-mortem 7, third batch -- re-ran find_review_candidates.py after the
    # first two batches; the pool re-ranks as rows get classified, so re-running surfaces a genuinely new
    # top slice each time rather than the same rows).
    (r"declaration of interest\b", "'Declaration of Interest' disclosure-statement boilerplate"),
    (r"ethics approval and consent to participate",
     "BioMed Central/Springer standard 'Declarations' block boilerplate (ethics/consent/competing-interests)"),
    (r"article 25fa\b.{0,15}dutch copyright act", "Dutch 'Taverne'/Article 25fa copyright-license boilerplate"),
    (r"supplementary material\b.{0,40}can be found online",
     "Frontiers 'Supplementary material...can be found online' notice boilerplate"),
    (r"granted medrxiv a license to display the preprint in perpetuity", "medRxiv preprint license boilerplate"),
    (r"queen.s printer and controller of hmso", "UK HMSO monograph copyright boilerplate"),
    (r"tous droits réservés\b.{0,20}sauf mention contraire",
     "French journal-platform copyright footer boilerplate (Cairn/OpenEdition-style)"),
    (r"edited by p\.?\s*kangueane", "Bioinformation journal citation-block template (same editor on every issue)"),
    # Added 2026-08-24 (todo.md post-mortem 7, fourth batch -- switched from lcs-ranked to
    # ngram_jaccard-minus-lcs_ratio-ranked sampling, find_review_candidates.py --sort gap).
    (r"publications can be used in (their|the candidate.s) thesis in lieu of a chapter",
     "university 'thesis by publication' policy-declaration boilerplate (same standard clause reused across "
     "many different theses at the same institution)"),
    # Added 2026-08-24 (todo.md's "check the lcs=1/ngram=1 rows" investigation): institutional-repository
    # rights/take-down-policy boilerplate, attached to EVERY paper deposited via that repository -- these
    # recur across many-to-many pairs (not just one pair), the same corpus-wide-reuse signature as the
    # other repository-deposit-stamp patterns already above (Glasgow Enlighten), just different repositories.
    (r"general rights\b.{0,20}copyright and moral rights for the publications made accessible",
     "TU Eindhoven repository 'General rights' deposit boilerplate"),
    (r"please check the document version of this publication",
     "TU Eindhoven repository document-version notice boilerplate"),
    (r"take down policy\b.{0,20}if you believe that this document breaches copyright",
     "TU Eindhoven repository take-down-policy boilerplate"),
    (r"take-down policy\b.{0,20}if you believe that this material infringes your copyright",
     "Erasmus University Rotterdam repository take-down-policy boilerplate"),
    (r"this is the accepted version of the paper\.\s*this version of the publication may differ",
     "'accepted version of the paper' repository deposit notice boilerplate"),
    (r"creative commons licen[cs]e, you must assume that re-use is limited to personal use",
     "repository-specific CC-license paraphrase boilerplate ('re-use is limited to personal use')"),
    (r"funding open access funding provided by scelc", "SCELC library-consortium open-access funding boilerplate"),
    (r"administrative committee \(adcom\) members",
     "IEEE Reliability Society newsletter 'AdCom Members' masthead boilerplate (same committee roster reused across issues)"),
    (r"palgrave.{0,5}and macmillan.{0,5}are registered trademarks", "Palgrave Macmillan book front-matter trademark boilerplate"),
    (r"this work is in copyright\.\s*it is subject to statutory exceptions",
     "Cambridge University Press book/handbook copyright-page boilerplate"),
    (r"the publication ethics committee of.{0,5}redfame publishing",
     "Redfame Publishing standard ethics-approval statement boilerplate"),
    (r"how to cite tspace items", "University of Toronto TSpace repository citation-notice boilerplate"),
    # Added 2026-08-24 (lcs=1/ngram=1 investigation, continued past entry 200): two more
    # government/institutional report-series boilerplate families, each recurring across many
    # unrelated report pairs from the same publisher/series -- same signature as the DOE/NTIS/GPO
    # patterns already above, just different distinctive substrings.
    (r"eurocard,\s*mastercard,\s*solo,\s*switch and visa",
     "NCCHTA/HTA monograph series ordering-information boilerplate (accepted payment cards)"),
    (r"payable to university of southampton and drawn on a bank",
     "NCCHTA/HTA monograph series ordering-information boilerplate (cheque payment instructions)"),
    (r"gray publishing,\s*tunbridge wells,\s*kent",
     "NCCHTA/HTA monograph series publisher-imprint boilerplate (Gray Publishing, Tunbridge Wells)"),
    (r"gaithersburg,\s*md\s*20899.{0,30}boulder,\s*co\s*80303",
     "NIST institute-locations address boilerplate"),
    (r"sandia is a multiprogram laboratory operated by sandia corporation",
     "Sandia National Laboratories DOE-contract funding/imprint boilerplate"),
    (r"5285 port royal rd",
     "NTIS mailing-address/ordering boilerplate (distinct wording from the 'national technical "
     "information service' pattern above -- this is the address block, not the spelled-out name)"),
    # Added 2026-08-24 (lcs=1/ngram=1 investigation, fresh re-query after applying the batch above):
    # a Springer Nature CC-BY permission paragraph appearing verbatim across many unrelated article
    # pairs (same standard rights-and-permissions boilerplate every Springer CC-BY article carries,
    # same signature as the 'jurisdictional claims' Publisher's Note pattern above but a different
    # paragraph of that same standard front/back matter).
    (r"permitted by statutory regulation or exceeds the permitted use",
     "Springer Nature standard CC-BY rights-and-permissions boilerplate paragraph"),
    (r"international journal of computer science and mobile computing",
     "IJCSMC journal masthead boilerplate"),
    # Added 2026-08-24 (lcs=1/ngram=1 investigation, continued): three more recurring boilerplate
    # families -- a Turkish journal's standard funding/conflict-of-interest disclosure clause (found
    # across a cluster of Turkish nursing/health-ethics journal papers), an IEEE Computer Society
    # advertising-filler page reused across many unrelated proceedings issues, and a German academic-
    # repository (SSOAR/peDOCS-style) standard non-commercial-use licence clause.
    (r"herhangi bir destek alınmamıştır|yazar katkıları\b",
     "Turkish journal standard funding-disclosure/author-contributions boilerplate clause"),
    (r"computer society jobs board", "IEEE Computer Society Jobs Board advertising-filler page "
     "(matches with or without the leading 'IEEE' -- the masthead text itself varies between issues)"),
    (r"sie dürfen die dokumente nicht für öffentliche oder kommerzielle zwecke",
     "German academic-repository (SSOAR/peDOCS-style) standard non-commercial-use licence clause"),
    # Added 2026-08-24 (random-200 round 3, at the user's request -- a fresh random sample of the pool
    # after the lcs=1/ngram=1-targeted sixth batch above, same discipline: validate each pattern with a
    # unit test before touching the real DB).
    (r"attribution\s*[–-]\s*you must cite the work\.\s*translations\s*[–-]\s*you must cite the original",
     "OECD/Brookings-style CC-license 'Attribution...Translations' standard clause"),
    (r"federal reserve bank of atlanta working papers",
     "Federal Reserve Bank of Atlanta working-paper-series footer boilerplate"),
    (r"this cc license does not apply to non-adb copyright materials",
     "Asian Development Bank (ADB) publication standard CC-license disclaimer"),
    (r"intechopen\.com|hotel equatorial shanghai",
     "InTechOpen book/chapter masthead boilerplate (open-access book publisher contact block, reused "
     "verbatim across every chapter of every InTech-published book)"),
    (r"drittmaterial\s*unterliegen",
     "German journal standard 'Drittmaterial...CreativeCommonsLizenz' rights clause (frequently loses "
     "its spaces entirely in pdftotext output on justified-column layouts -- matched loosely enough to "
     "survive that)"),
    # Added 2026-08-24 (random-200 round 3, continued reading past entry 83).
    (r"researchonline\.lshtm\.ac\.uk", "LSHTM (London School of Hygiene & Tropical Medicine) institutional-repository licence-notice boilerplate"),
    (r"candidate.s declaration\b.{0,20}i declare that.{0,20}i have complied with the unsw thesis examination procedure",
     "UNSW thesis-examination 'Candidate's Declaration' boilerplate (same standard clause reused across many different UNSW theses)"),
    (r"located at boulder,\s*co\s*80303", "NIST/NBS institute-locations boilerplate (broader address-only variant -- "
     "distinct wording from the 'Gaithersburg, MD 20899...Boulder, CO 80303' pattern above, which requires the ZIP code "
     "this variant omits)"),
    (r"standard reference materials.{0,10}and provides calibration services\. the laboratory consists of the following centers",
     "NIST/NBS 'National Engineering Laboratory' standard mission-statement boilerplate"),
    (r"fate in animals \(rats\), fate in plants", "EPA pesticide risk-assessment fact-sheet standard study-list template (same boilerplate list of required toxicology study categories, reused across every pesticide's registration document)"),
    (r"xsl.fo renderx", "JMIR journal XML-rendering-pipeline artifact text (not article content -- leftover from the PDF-generation toolchain)"),
    (r"funding for sdss-iii has been provided by the alfred p\.?\s*sloan foundation",
     "SDSS-III standard funding-acknowledgment boilerplate (reused verbatim across every paper using SDSS-III survey data)"),
    (r"sciencepubco\.com/index\.php/ijet",
     "International Journal of Engineering & Technology (IJET/SciencePubCo) journal masthead boilerplate "
     "(recurs across many unrelated IJET issues in this corpus, each sharing the same masthead line)"),
    (r"hillpublisher\.com/journals/jhass",
     "Journal of Humanities, Arts and Social Science (Hill Publishing) journal masthead boilerplate"),
    # Added 2026-08-24 (random-200 round 4, at the user's request -- another fresh random sample).
    (r"in violation of ieee.s publication principles",
     "IEEE standard retraction-notice boilerplate (the same fixed wording is reprinted on every "
     "IEEE-retracted paper regardless of topic, so two unrelated retracted papers will always share it)"),
    (r"brics publications are in general accessible through the world wide web",
     "BRICS (Basic Research in Computer Science, Aarhus) technical-report-series footer boilerplate"),
    (r"brought to you by the faculty of law at epublications@bond",
     "Bond University 'ePublications@bond' law-repository deposit-notice boilerplate"),
    (r"brought to you for free and open access by the institute of clinical bioethics",
     "Journal of Healthcare Ethics & Administration (JHEA) / Saint Joseph's University repository deposit-notice boilerplate"),
    (r"faculté de droit, section de droit civil, université d.ottawa",
     "Université d'Ottawa Faculté de droit (civil law section) repository copyright-notice boilerplate"),
    (r"operated by the university of california for the united states department of energy under contract w-7405-eng-36",
     "Los Alamos National Laboratory standard DOE-contract funding/imprint line (same signature as the Sandia National Laboratories pattern above, different lab and contract number)"),
    (r"prepared as an account of work sponsored by an agency of the united states government",
     "standard DOE/national-laboratory government-report disclaimer boilerplate (the 'neither the "
     "Regents...nor any agency thereof...make any warranty' liability disclaimer reused verbatim across "
     "reports from many different DOE national laboratories, not just one specific lab)"),
    (r"national renewable energy laboratory\b.{0,20}1617 cole boulevard",
     "NREL (National Renewable Energy Laboratory) standard address/imprint boilerplate"),
    (r"aaai symposium committee", "AAAI Symposium Committee roster boilerplate (reused across multiple AAAI News issues)"),
    (r"combinatorialpress\.com/jcmcc",
     "Journal of Combinatorial Mathematics and Combinatorial Computing (JCMCC) masthead boilerplate"),
    (r"download articles and share them with others as long as they credit the authors and the publisher",
     "generic publisher CC BY-NC-ND-style permission paraphrase boilerplate"),
    # Added 2026-08-24 ("manually check the last 1200" -- full-pool systematic review, batch 1).
    (r"eprints@whiterose\.ac\.uk", "White Rose Research Online (Leeds/Sheffield/York) institutional-repository deposit-notice boilerplate"),
    (r"vetted through the ieee crosscheck portal", "IEEE conference call-for-papers/submission-policy filler boilerplate"),
    (r"gjetr\.ep-journals\.org", "Global Journal of Engineering and Technology Review (GJETR) masthead boilerplate"),
    (r"uchicago argonne, llc,?\s*operator of argonne national laboratory",
     "Argonne National Laboratory standard DOE-contract funding/imprint line (same signature as the Sandia/LANL patterns above, different lab)"),
    (r"personal use is also permitted, but republication/redistribution requires ieee permission",
     "generic IEEE copyright/reuse-permission notice boilerplate"),
    (r"host your own frontiers research topic", "Frontiers journal 'Research Topic' editorial-office contact boilerplate"),
    (r"the university recognises that there may be exceptional circumstances requiring restrictions on copying",
     "UNSW thesis restricted-access declaration boilerplate (same standard front-matter block as the "
     "'Candidate's Declaration' pattern above, different paragraph of the same document template)"),
    # Added 2026-08-24 ("manually check the last 1200", batch 2).
    (r"itsi transactions on electrical and electronics engineering", "ITSI Transactions journal masthead boilerplate"),
    (r"bioexcel publishing limited is registered in england", "BioExcel Publishing (Drugs in Context journal) standard imprint/contact boilerplate"),
    (r"watts s\.? humphre?y software quality award", "IEEE Computer Society 'Watts S. Humphrey Software Quality Award' nomination-announcement filler boilerplate"),
    (r"the author retains ownership of the copyright in this thesis",
     "generic university thesis copyright-retention boilerplate"),
    (r"ich erkläre hiermit ehrenwörtlich, dass ich die vorliegende arbeit selbstständig angefertigt",
     "German university thesis sworn-declaration (Eidesstattliche Erklärung) boilerplate"),
    (r"sandia national laboratories is a multi-?program laboratory",
     "Sandia National Laboratories DOE-contract funding/imprint line (alternate wording -- distinct from "
     "the 'Sandia is a multiprogram laboratory operated by Sandia Corporation' pattern above, which this "
     "phrasing doesn't match verbatim)"),
    (r"does not necessarily constitute or imply its endorsement, recommendation or favouring",
     "standard government/agency trade-name-disclaimer boilerplate"),
    # Added 2026-08-24 ("manually check the last 1200", batch 3).
    (r"the materials of the conference will be published in the journals theory of state and law",
     "Russian legal-conference proceedings standard journal-list boilerplate"),
    (r"declaration of generative ai in scientific writing",
     "standard 'Declaration of Generative AI in Scientific Writing' disclosure-clause boilerplate"),
    (r"do not necessarily reflect the views of the icrc",
     "ICRC journal standard views-disclaimer boilerplate"),
    (r"pubsmarketing@adb\.org", "Asian Development Bank (ADB) publication standard contact/permissions boilerplate (distinct from the earlier ADB CC-license disclaimer pattern above)"),
    (r"this philadelphia fed working paper represents preliminary research",
     "Federal Reserve Bank of Philadelphia working-paper-series disclaimer boilerplate"),
    # Added 2026-08-24 ("manually check the last 1200", batch 4).
    (r"the frontiers journal series is a multi-tier and interdisciplinary set of",
     "Frontiers journal-series standard front-matter boilerplate (distinct from the 'host your own Frontiers Research Topic' pattern above -- a different standard paragraph of the same publisher's front matter)"),
    (r"chatham house, the royal institute of international affairs, is a world-leading policy institute",
     "Chatham House standard imprint/mission-statement boilerplate"),
    (r"apsdpr\.org", "Africa's Public Service Delivery and Performance Review (APSDPR) journal masthead boilerplate"),
    (r"locs offers open access options for authors", "IEEE Letters of the Computer Society (LOCS) standard open-access notice boilerplate"),
    (r"researchonline\.lse\.ac\.uk", "LSE Research Online institutional-repository deposit-notice boilerplate"),
    # Added 2026-08-24 ("manually check the last 1200", batch 5).
    (r"sinergi international journal of islamic studies", "Sinergi International Journal of Islamic Studies masthead boilerplate (recurs across many unrelated issues of this journal in the corpus)"),
    (r"international journal on science and technology \(ijsat\)", "IJSAT (International Journal on Science and Technology) journal masthead boilerplate"),
    (r"eduzone:\s*international peer reviewed", "EDUZONE journal masthead boilerplate"),
    (r"leibniz international proceedings in informatics", "LIPIcs (Leibniz International Proceedings in Informatics) / Schloss Dagstuhl standard imprint boilerplate"),
    (r"pasupuleti,\s*mk\s*2023", "recurring self-citation to 'Pasupuleti, MK 2023...Topological and Software-Driven Advances in Robotics' reused as a throwaway reference across many unrelated AI survey/book-chapter papers"),
    # Added 2026-08-24 ("manually check the last 1200", batch 6).
    (r"ieee computer society (member benefits|has you covered)",
     "IEEE Computer Society membership advertising-filler page boilerplate"),
    (r"n\.b\.\s*when citing this work, cite the original published paper",
     "generic IEEE 'cite the original published paper' notice boilerplate"),
    (r"stratford peer reviewed journals and book publishing", "Stratford Peer Reviewed Journals masthead boilerplate"),
    (r"the article was submitted to be part of a guest-edited issue\. an investigation by the publisher found a number of articles",
     "Hindawi-style mass-retraction notice boilerplate (identical wording reused across many unrelated retracted articles from the same publisher investigation)"),
    (r"www\.aaai\.org/symposia", "AAAI Symposia registration/hotel-logistics announcement boilerplate (reused across different AAAI News issues)"),
    # Added 2026-08-30 (hindawi/nursing/anthropology field-corpus review, first pass): sampling the
    # top of the unclassified cross-author queue by similarity/lcs_ratio across the three new field
    # corpora found the same handful of boilerplate families dominating it -- none of the existing
    # patterns matched these specific phrasings. Ethics/data-availability disclaimers are Hindawi
    # journal-article "Declarations" boilerplate (distinct wording from the BioMed Central/Springer
    # "ethics approval and consent to participate" pattern already above); the rest are thesis
    # front-matter/repository-footer boilerplate specific to this anthropology corpus's heavy
    # thesis/dissertation composition.
    (r"ethical standards of the institutional and/or national research committee and with the 1964 helsinki",
     "Hindawi-style 'Ethical Approval...1964 Helsinki declaration' Declarations-block boilerplate"),
    (r"data availability\b.{0,20}the data used to support the findings of this study",
     "Hindawi-style 'Data Availability...used to support the findings of this study' Declarations-block boilerplate"),
    (r"with whom i have worked at unsw or elsewhere,?\s*is explicitly acknowledged",
     "UNSW thesis 'Any contribution made to the research by others...is explicitly acknowledged' "
     "declaration boilerplate (distinct paragraph of the same front-matter template as the "
     "'Candidate's Declaration' pattern already above -- this one recurs mid-sentence across paragraph "
     "splits, so matched on the clause common to every observed fragment rather than the full sentence)"),
    (r"jstor is a not-for-profit service that helps scholars",
     "JSTOR digital-archive footer boilerplate (reused verbatim on every JSTOR-hosted scan this corpus retrieved)"),
    (r"researchspace\.bathspa\.ac\.uk",
     "Bath Spa University 'ResearchSPAce' institutional-repository pre-published-version notice boilerplate"),
    (r"indicate whether this thesis contains published material or not",
     "UK thesis-by-publication front-matter checkbox declaration boilerplate ('contains published material or not')"),
    (r"received funding under the european union.s h2020 research and innovation programme",
     "EU H2020 grant-acknowledgment boilerplate (found 2026-09-04 reviewing computer-ethics corpus's "
     "SIENNA project reports -- the exact grant-agreement number varies per project, so matched on the "
     "fixed acknowledgment phrasing common to every H2020-funded paper's funding statement, not the "
     "project name or grant number)"),
    # 2026-09-04: the following batch was found by 6 subagents doing a bigger review pass across
    # computer-ethics's full same_author=0 backlog (see LEAD_AGENT_PLAYBOOK.md's Phase 3/4) -- each
    # entry below had concrete verbatim example text reported; patterns described only generically
    # (no exact wording given) were left out and noted in todo.md instead rather than guessed at.
    (r"academia\.edu reserves the right,? at its sole discretion,? to discontinue or terminate",
     "Academia.edu Terms-of-Service boilerplate"),
    (r"this paper is part of .{0,80},? a special issue",
     "Internet Policy Review (or similar) special-issue editorial credit line -- the special issue's "
     "own title varies per issue, so matched on the fixed framing phrase around it, not any one issue name"),
    (r"paste the appropriate copyright.license statement here\. acm now supports three different",
     "ACM manuscript template placeholder left unfilled by the author(s) -- not real content at all"),
    (r"this is a pdf file of an article that has undergone enhancements after acceptance",
     "Elsevier 'accepted manuscript' early-access disclaimer boilerplate"),
    (r"in consultation with the editor.in.chief,? therefore no longer has confidence",
     "BMC/Springer retraction-notice boilerplate"),
    (r"the collection of personal information shall be limited to that which is necessary",
     "Fair Information Practice Principles canonical text, quoted verbatim across unrelated privacy papers"),
    (r"competing interests:? no competing interests were disclosed\. i confirm that i have read",
     "F1000Research standard peer-review-report boilerplate"),
    (r"reproduced with permission of the copyright owner\.? further reproduction prohibited without",
     "ProQuest/UMI dissertation-reproduction copyright notice"),
    (r"photographs,? print bleed.through,? substandard margins",
     "ProQuest/UMI dissertation-scanning quality notice (distinct from the reproduction-copyright notice "
     "above -- both are ProQuest boilerplate but different paragraphs of it)"),
    (r"erlaubnis ist jede urheberrechtliche nutzung untersagt,? insbesondere die nutzung des inhalts "
     r"im zusammenhang mit,? f.r oder in ki.systemen",
     "German AI-training/generative-model opt-out clause, found on a German-language paper -- likely to "
     "recur on other German papers from the same publisher/repository software"),
]
_COMPILED_TEXT_PATTERNS = [(re.compile(p, re.IGNORECASE), label) for p, label in TEXT_PATTERNS]

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
# Added 2026-09-04, found reviewing computer-ethics corpus cross-author candidates: a Springer-style
# running page header/footer -- "<Author> (&) <Department>, <Address> e-mail: <address> 123 <Journal>
# (<Year>) <Vol>:<Pages> DOI <doi>" -- gets reprinted on every page of the source PDF and, after
# extraction, bleeds into whatever body paragraph happens to fall on that page boundary. The existing
# ">= 2 emails" rule below only catches this for a MULTI-author paper's header; a solo-author paper's
# header has exactly one email and slipped through to a manual-review bucket in several real cases
# (confirmed: Coeckelbergh, White, Biller-Andorno single-author papers, each showing "ai_check=yes"
# only via the coarser same_author=1 blanket rule rather than being identified as a header artifact by
# name). The combination of an email address AND a DOI-shaped citation string in the same paragraph is
# the reliable signal -- real body prose essentially never contains both; a DOI on its own can appear in
# a citation, but not paired with an email in the same paragraph, which is specifically a header/footer.
DOI_RE = re.compile(r"\bdoi[:\s]+10\.\d{4,9}/\S+", re.IGNORECASE)
# Added 2026-08-30: a shared-bibliography-entry signal for citation lists that don't match either
# existing bibliography pattern -- no leading number (the "^\d{1,3}\." numbered-entry pattern) and no
# "et al" immediately followed by a year (the "et al" pattern requires the year right after "et al",
# which misses "Lastname CW, Lastname Y, ... et al.: Title. Journal Year" -- the year comes after a
# title, not immediately). Requires >=2 "Lastname Initials," pairs (initials = 1-3 ALL-CAPS letters,
# optionally period-separated, e.g. "Wu J.," "Zhang K.C.," "Chen Y,") within the first 200 chars --
# validated against a real hand-read sample of anthropology's unclassified-candidate pool (todo.md's
# field-corpus review) and against risky ordinary-prose lookalikes ("the U.S., U.K., and E.U. markets",
# "sites in NY, CA, TX, and FL", "cohorts A, B, and C") that all score 0 because the word before the
# initials must contain a lowercase letter, which those short all-caps abbreviations never do.
LASTNAME_INITIALS_RE = re.compile(
    r"\b[A-ZÀ-ÖА-Я][a-zà-öа-я]+\s+[A-Z]{1,3}\.(?:[A-Z]\.)?,|\b[A-ZÀ-ÖА-Я][a-zà-öа-я]+\s+[A-Z]{1,3},"
)
MONTH_YEAR_RE = re.compile(r"\((jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*(19|20)\d{2}\)", re.IGNORECASE)
# Added 2026-08-24: MONTH_YEAR_RE alone missed a whole cluster of recurring-report-series titles that
# spell the date as ", <Month>[-<Month2>][/<Month2>] <Year>[. Revision N]" with no parentheses at all
# (e.g. "Environmental regulatory update table, January 1990" vs "...May 1990") -- found by hand-reading
# a ranked sample (todo.md post-mortem 7). These were falling through to a manual "review" bucket purely
# because the date format didn't match, not because the titles were actually different in substance.
BARE_MONTH_YEAR_RE = re.compile(
    r",?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"(\s*[-–—/]+\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?)?"
    r"\s+(19|20)\d{2}\.?\s*(revision\s*\d*|rev\.?\s*\d*)?\s*$",
    re.IGNORECASE,
)
TRAILING_YEAR_RE = re.compile(r"\(?\b(19|20)\d{2}\b\)?\s*$")


def classify_text_patterns_only(text):
    """Just the curated TEXT_PATTERNS regex list -- none of classify_text()'s
    generic fallback heuristics (year-count/email-count/leading-quote-mark).
    Exists for embed_paragraphs.py's pre-embedding boilerplate skip: those
    fallbacks were designed and validated only in classify_text()'s original
    context (a candidate PAIR that already cleared the 0.85 cosine-similarity
    bar for build_dupe_candidates.py -- "2+ citation years" or "2+ emails" is
    a reasonable last-resort signal once you already know two paragraphs are
    suspiciously similar). Applied to every paragraph in the corpus *before*
    any pairing exists, those same heuristics would misfire on plenty of
    genuinely original text (a paragraph discussing two historical dates, an
    author-contact section that's part of real content) and -- unlike a
    wrongly-classified candidate row, which a human can still catch in
    review_dupes.py -- a paragraph skipped at embed time never gets a second
    chance at any pairing at all. The curated regex patterns don't have that
    problem: each one was validated against a real hand-read example before
    being added, so they're safe to trust unconditionally, pre-pairing."""
    for rx, label in _COMPILED_TEXT_PATTERNS:
        if rx.search(text):
            return label
    return None


def classify_text(text):
    label = classify_text_patterns_only(text)
    if label:
        return label
    if len(YEAR_RE.findall(text)) >= 2:
        return "shared bibliography entries (multiple citation years in one paragraph)"
    if EMAIL_RE.search(text) and DOI_RE.search(text):
        return "running page header/footer (email address + DOI in one paragraph, not content)"
    if len(EMAIL_RE.findall(text)) >= 2:
        return "author affiliation/contact block (2+ email addresses), not content"
    if len(LASTNAME_INITIALS_RE.findall(text[:200])) >= 2:
        return "shared bibliography/citation entry (2+ 'Lastname Initials,' author-list pairs near start)"
    if text.strip().startswith('"') or text.strip().startswith("“"):
        return "quoted excerpt from a cited external source (starts with a quotation mark)"
    return None


def strip_trailing_date(title):
    t = MONTH_YEAR_RE.sub("", title)
    t = BARE_MONTH_YEAR_RE.sub("", t)
    t = TRAILING_YEAR_RE.sub("", t)
    return t.strip().rstrip(",").strip()


def alpha_fraction(text):
    letters = sum(c.isalpha() for c in text)
    return letters / max(len(text), 1)


DEFAULT_LOW_OVERLAP_THRESHOLD = 0.05


def classify_row(text1, text2, title1, title2, lcs_ratio=None, ngram_jaccard=None):
    """Returns (verdict, reason) or (None, None) if not programmatically classifiable.

    `lcs_ratio`/`ngram_jaccard` can be passed in already-computed (build_dupe_candidates.py
    persists both on every potential_dupes row) to avoid recomputing them; if omitted, they're
    computed here from the text, same as before this parameter existed."""
    reason = classify_text(text1) or classify_text(text2)
    if reason:
        return "no", reason

    if lcs_ratio is None:
        lcs_ratio = to.longest_common_word_run(text1, text2)
    if ngram_jaccard is None:
        ngram_jaccard = to.ngram_jaccard(text1, text2)

    # Added 2026-08-24 (todo.md's "another pass" investigation, second random-200 sample):
    # near-zero on BOTH overlap metrics despite the >=0.85 cosine similarity build_dupe_candidates.py
    # requires to even generate a candidate -- validated by hand-reading 30 random examples at this
    # threshold, all 30 were coincidental same-topic-different-content or non-English embedding noise
    # (all-MiniLM-L6-v2 is English-tuned; a large fraction of these were non-English pairs where cosine
    # similarity clusters high but the actual words share almost nothing). This is deliberately a much
    # stricter threshold than the numeric/tabular check below (0.05 vs 0.3) -- validated at 0.05, not
    # assumed to generalize to 0.3, and applies regardless of alpha_fraction (text or numeric content).
    if lcs_ratio < DEFAULT_LOW_OVERLAP_THRESHOLD and ngram_jaccard < DEFAULT_LOW_OVERLAP_THRESHOLD:
        return "no", (
            f"coincidental embedding-only similarity -- near-zero real textual overlap "
            f"(lcs_ratio={lcs_ratio:.3f}, ngram_jaccard={ngram_jaccard:.3f}) despite high cosine similarity; "
            f"same-topic-different-content or non-English embedding noise, not copied text"
        )

    if alpha_fraction(text1) < 0.5 and alpha_fraction(text2) < 0.5:
        if lcs_ratio < 0.3 and ngram_jaccard < 0.3:
            return "no", f"numeric/tabular content, embedding similarity unreliable here (lcs_ratio={lcs_ratio:.2f}, ngram_jaccard={ngram_jaccard:.2f})"

    # Fixed 2026-09-05 (found by hand while triaging the whole-prefix IAEME/Pearl Blue sweep --
    # see todo.md's "7th paper-mill case" entry): this used to fire whenever the *stripped* titles
    # matched, without checking that a date/edition token was actually found and removed from
    # either side. Two titles that are already identical *before* stripping anything pass that same
    # test -- but an identical title with zero dating information isn't a benign "different edition
    # of the same report," it's the exact paper-mill signature (same title, different name on it,
    # nothing to tell the "editions" apart) this project is looking for. Now requires the titles to
    # differ before stripping (so there's an actual date/edition difference to explain) AND that
    # stripping changed at least one side (so a real date/edition token was found, not just an
    # accidental substring match) before trusting title-equality-after-stripping as "same series."
    t1_stripped, t2_stripped = strip_trailing_date(title1), strip_trailing_date(title2)
    title1_norm, title2_norm = title1.strip().lower(), title2.strip().lower()
    titles_already_equal = title1_norm == title2_norm
    something_stripped = (t1_stripped.lower() != title1_norm) or (t2_stripped.lower() != title2_norm)
    if not titles_already_equal and something_stripped and t1_stripped and t1_stripped.lower() == t2_stripped.lower():
        return "no", f"sequential editions of the same recurring report/index series ({t1_stripped!r})"

    return None, None


def run(conn, logger_=None):
    rows = conn.execute(
        """
        SELECT pd.id, pd.same_author, p1.title, p2.title, pr1.text, pr2.text,
               pd.lcs_ratio, pd.ngram_jaccard
        FROM potential_dupes pd
        JOIN paragraphs pr1 ON pr1.id = pd.paragraph_id_1
        JOIN paragraphs pr2 ON pr2.id = pd.paragraph_id_2
        JOIN papers p1 ON p1.id = pd.paper_id_1
        JOIN papers p2 ON p2.id = pd.paper_id_2
        WHERE pd.same_paper = 0 AND pd.ai_check IS NULL
        """
    ).fetchall()

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    counts = {"no": 0, "yes": 0, "unclassified": 0}
    for _id, same_author, title1, title2, text1, text2, lcs_ratio, ngram_jaccard in rows:
        verdict, reason = classify_row(text1, text2, title1, title2, lcs_ratio, ngram_jaccard)
        if verdict is None and same_author:
            verdict, reason = "yes", "programmatic (same_author default): self-reuse across two of the same author's papers"
        if verdict is None:
            counts["unclassified"] += 1
            continue
        conn.execute(
            "UPDATE potential_dupes SET ai_check = ?, ai_check_reason = ?, ai_checked_at = ? WHERE id = ?",
            (verdict, reason, now, _id),
        )
        counts[verdict] += 1
    conn.commit()

    if logger_:
        logger_.info("classified %d candidate(s): %d no, %d yes, %d left for manual review (same_author=0, no pattern matched)",
                      len(rows), counts["no"], counts["yes"], counts["unclassified"])
    return counts


def parse_args():
    parser = argparse.ArgumentParser(description="Programmatic first pass at potential_dupes.ai_check.")
    parser.add_argument("--library-db", type=Path, default=Path("library.sqlite3"))
    return parser.parse_args()


def main():
    args = parse_args()
    conn = db.connect(args.library_db)
    run(conn, logger_=logger)
    conn.close()


if __name__ == "__main__":
    main()
