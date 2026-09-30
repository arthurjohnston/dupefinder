# Example output

These pages are example output from this project's comparison tools
(`write_dupe_reports_html.py`, built on `compare_two_papers.py`'s word-shingle matcher). Each one shows
the text two publicly available documents have in common, rendered as a word-level diff. They were
produced while developing and testing the pipeline, and are here so you can see what the tools produce
on real documents.

**A page shows measured text overlap. It does not establish why the overlap exists.** Shared text has many
legitimate explanations: the same authors publishing the same work twice, a preprint and its published
version, a shared institutional template, properly licensed reuse, or a retrieval error. None of these
pages is a finding of misconduct by any named person, and none has been adjudicated by anyone, except
where a publisher has already acted publicly.

Open any page directly in a browser. Each is a self-contained HTML fragment with no external resources.

The pages use `write_dupe_reports_html.py --neutral` wording: a "Text-overlap comparison" heading, a
two-way arrow between the documents rather than a directional one, and no verdict labels. The red/green
diff and every figure are exactly as originally rendered.

## Layout

- `full_copies/`: pairs where at least 80% of one document's words fall inside text shared with
  the other. The documents are substantially the same text.
- `partial_overlap/`: pairs where less than that is shared, but still at least one long verbatim passage.
- A subdirectory inside either one groups several pairs from one related set of documents. A group
  whose pairs fall on both sides of the 80% line appears in both.
- Every pair in `full_copies/` is shown as one continuous word-level diff of the two documents end to
  end. Where some shared text sits at a different position in each document, which an in-order diff
  cannot place, the pair also has a *per-passage view* listing each matched passage separately.
- Pages in `partial_overlap/` use either view: some are a single diff, others list the matched passages.


## How the figures below were measured

Every pair was re-measured the same way for this table: 10-word shingles, lockstep X-drop extension of 3,
over each document's full extracted text. "Shared text" is the share of each document's words that fall
inside a matched run. A page may print a slightly different figure because some pages were rendered with
other settings (the page footer names them).

Each pair was also checked for the usual non-copying explanations before being included:

- **Same authors on both sides.** Checked against both the extracted metadata and the publisher's own
  Crossref record, with names normalized for order, accents and honorifics, plus a looser surname-and-initial
  match. Pairs that share any author are not included here.
- **Shared text that doesn't show copying between the two.** Pairs where most of the shared text turned
  out to be a publisher's own template, journal boilerplate, or text both documents took from a common
  outside source (such as a widely copied tutorial) were removed.
- **The same document cataloged twice.** No pair shares a DOI.
- **A retrieval error** where one record actually contains the other document's PDF: none found.
- **Citation.** Where one document cites the other, that is noted below. A citation does not by itself
  mean the reused text is attributed, so those pairs remain.

Where a publisher has already acted publicly: the later document in `CE-N05` was retracted by Cureus on
2017-12-13 (retraction notice 10.7759/cureus.r9).

Documents that cite their counterpart: `CE-N14` (the later document lists the earlier one in its
references).

## Pairs where the shared material is data

In most pairs the shared material is prose. In these three it also includes measured or surveyed
data: counts, percentages and test statistics that two separate studies would not normally be expected
to report identically.

- **AN-03, low birth weight** ([page](full_copies/AN-03-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html)). Two 2024 articles in the International Journal
  of Pharmaceutical and Clinical Research (vol. 16, issues 3 and 5) with the same title, different author
  lists with no author in common, and bylines at different medical colleges in Bihar (DMCH,
  Laheriasarai and JLNMC, Bhagalpur). Both report a study of 300 cases and 600 controls, and give the
  same counts and percentages to one decimal place, for example in the maternal-age table and the
  education and occupation breakdowns, alongside the same methods text.
- **CE-04, teachers' views on AI in education** ([page](partial_overlap/CE-04-teachers-perspectives-on-artificial-intelligence-in-educatio-vs-balancing-innovation-and-ethics-educators-perspectives-on-th-48-shingle-matches.html)). A March 2024 article in
  *Advances in Mobile Learning Educational Research* and a September 2024 article in *The American
  Journal of Social Science and Education Innovations*, with different titles and different authors.
  Both report a survey of 74 teachers with the same item-by-item frequency tables, means and standard
  deviations, the same independent-samples t-test and the same one-way ANOVA table (for example a
  between-groups sum of squares of 1356.645).
- **CE-N02, electric-vehicle ownership** ([page](partial_overlap/CE-N02-electric-vehicle-ownership-in-kerala-insights-on-brand-choic-vs-electric-vehicles-in-india-bridging-the-gap-between-expectat-22-shingle-matches.html)). A 2023 article on EV ownership
  in Kerala and a 2025 article on EVs in India, in two different IAEME journals with different
  authors. They share the same demographic table for 100 respondents (income bands, area of
  residence), the same summary figures (58% female, 64% aged 18 to 25) and the same methodology
  paragraph, which in both articles describes the sample as EV users in Kerala's Thiruvananthapuram
  district.

## Grouped by publisher

Pairs are grouped by where the two documents were published, taken from each DOI's registration
record (Crossref, or DataCite for repository and preprint deposits). "Full copy" pages are in
`full_copies/` and "partial" pages in `partial_overlap/`, as described above.

### Both documents: Zestera Publications (16 pairs)

| case | page | overlap | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|
| CE-36 | [CE-36-a-novel-ensemble-deep-learning-approach-for-accurate-credit--vs-hybrid-ensemble-and-deep-learning-approach-for-improved-cred.html](full_copies/CE-36-a-novel-ensemble-deep-learning-approach-for-accurate-credit--vs-hybrid-ensemble-and-deep-learning-approach-for-improved-cred.html) | full copy | 96% / 93% | 4,839 / 4,982 |
| CE-37 | [37-140649-140665-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html](full_copies/CE-37-insight-system/37-140649-140665-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html) | full copy | 96% / 91% | 5,765 / 6,027 |
| CE-37 | [37-140649-140684-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html](full_copies/CE-37-insight-system/37-140649-140684-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html) | full copy | 94% / 91% | 5,765 / 5,980 |
| CE-37 | [37-140649-140684-per-run-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html](full_copies/CE-37-insight-system/37-140649-140684-per-run-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html) *(per-passage view)* | full copy | 94% / 91% | 5,765 / 5,980 |
| CE-38 | [38-140562-140693-design-and-implementation-of-an-ai-based-vulnerability-manag-vs-ai-powered-regulatory-compliance-checker-for-contracts.html](full_copies/CE-38-gift-zestera-student-projects/38-140562-140693-design-and-implementation-of-an-ai-based-vulnerability-manag-vs-ai-powered-regulatory-compliance-checker-for-contracts.html) | full copy | 81% / 80% | 4,614 / 4,643 |
| CE-38 | [38-140564-140604-study-planner-app-vs-ai-study-habit-analyzer.html](full_copies/CE-38-gift-zestera-student-projects/38-140564-140604-study-planner-app-vs-ai-study-habit-analyzer.html) | full copy | 91% / 92% | 4,316 / 4,251 |
| CE-38 | [38-140581-140679-agrisahayak-ai-based-smart-agriculture-management-dashboard--vs-agrisahayak-ai-based-digital-agriculture-assistant.html](full_copies/CE-38-gift-zestera-student-projects/38-140581-140679-agrisahayak-ai-based-smart-agriculture-management-dashboard--vs-agrisahayak-ai-based-digital-agriculture-assistant.html) | full copy | 93% / 92% | 3,873 / 3,888 |
| CE-38 | [38-140601-140657-debeats-full-stack-food-delivery-application-vs-busbee-real-time-school-bus-monitoring-system.html](full_copies/CE-38-gift-zestera-student-projects/38-140601-140657-debeats-full-stack-food-delivery-application-vs-busbee-real-time-school-bus-monitoring-system.html) | full copy | 84% / 80% | 4,562 / 4,821 |
| CE-34 | [34-advanced-agricultural-decision-system-using-recurrent-polyno-vs-advanced-agricultural-decision-system-using-recurrent-polyno.html](partial_overlap/CE-34-agricultural-decision-system/34-advanced-agricultural-decision-system-using-recurrent-polyno-vs-advanced-agricultural-decision-system-using-recurrent-polyno.html) | partial | 58% / 55% | 3,277 / 3,472 |
| CE-34 | [34b-140288-140417-a-data-driven-sound-analysis-approach-for-detecting-mechanic-vs-transformer-driven-wavlm-audio-modelling-for-robust-predicti.html](partial_overlap/CE-34-agricultural-decision-system/34b-140288-140417-a-data-driven-sound-analysis-approach-for-detecting-mechanic-vs-transformer-driven-wavlm-audio-modelling-for-robust-predicti.html) | partial | 59% / 50% | 3,344 / 4,007 |
| CE-35 | [CE-35-advanced-ai-facing-voting-system-vs-advanced-ai-facing-voting-system.html](partial_overlap/CE-35-advanced-ai-facing-voting-system-vs-advanced-ai-facing-voting-system.html) | partial | 74% / 60% | 3,115 / 3,834 |
| CE-38 | [38-140555-140566-ai-doctor-voice-amp-vision-vs-realestate-houseprice-prediction.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140555-140566-ai-doctor-voice-amp-vision-vs-realestate-houseprice-prediction.html) | partial | 63% / 62% | 3,861 / 3,940 |
| CE-38 | [38-140555-140566-per-run-ai-doctor-voice-amp-vision-vs-realestate-houseprice-prediction.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140555-140566-per-run-ai-doctor-voice-amp-vision-vs-realestate-houseprice-prediction.html) *(per-passage view)* | partial | 63% / 62% | 3,861 / 3,940 |
| CE-38 | [38-140567-140578-per-run-student-study-portal-vs-automated-examination-seating-arrangement-and-hall-allocatio.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140567-140578-per-run-student-study-portal-vs-automated-examination-seating-arrangement-and-hall-allocatio.html) *(per-passage view)* | partial | 61% / 66% | 3,454 / 3,273 |
| CE-38 | [38-140567-140578-student-study-portal-vs-automated-examination-seating-arrangement-and-hall-allocatio.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140567-140578-student-study-portal-vs-automated-examination-seating-arrangement-and-hall-allocatio.html) | partial | 61% / 66% | 3,454 / 3,273 |
| CE-38 | [38-140602-140673-design-and-implementation-of-smart-mart-an-ai-powered-e-comm-vs-design-and-implementation-of-home-deal-a-web-based-house-ren.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140602-140673-design-and-implementation-of-smart-mart-an-ai-powered-e-comm-vs-design-and-implementation-of-home-deal-a-web-based-house-ren.html) | partial | 45% / 40% | 4,922 / 5,164 |
| CE-38 | [38-140602-140673-per-run-design-and-implementation-of-smart-mart-an-ai-powered-e-comm-vs-design-and-implementation-of-home-deal-a-web-based-house-ren.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140602-140673-per-run-design-and-implementation-of-smart-mart-an-ai-powered-e-comm-vs-design-and-implementation-of-home-deal-a-web-based-house-ren.html) *(per-passage view)* | partial | 45% / 40% | 4,922 / 5,164 |
| CE-38 | [38-140606-140637-hospital-managemnet-system-vs-the-women-safety-tracker-app.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140606-140637-hospital-managemnet-system-vs-the-women-safety-tracker-app.html) | partial | 75% / 77% | 4,307 / 4,212 |
| CE-38 | [38-140618-140657-per-run-smart-inventory-and-product-management-system-vs-busbee-real-time-school-bus-monitoring-system.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140618-140657-per-run-smart-inventory-and-product-management-system-vs-busbee-real-time-school-bus-monitoring-system.html) *(per-passage view)* | partial | 31% / 34% | 5,320 / 4,821 |
| CE-38 | [38-140618-140657-smart-inventory-and-product-management-system-vs-busbee-real-time-school-bus-monitoring-system.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140618-140657-smart-inventory-and-product-management-system-vs-busbee-real-time-school-bus-monitoring-system.html) | partial | 31% / 34% | 5,320 / 4,821 |
| CE-38 | [38-140627-140693-design-and-implementation-of-an-ai-based-vulnerability-manag-vs-ai-powered-regulatory-compliance-checker-for-contracts.html](partial_overlap/CE-38-gift-zestera-student-projects/38-140627-140693-design-and-implementation-of-an-ai-based-vulnerability-manag-vs-ai-powered-regulatory-compliance-checker-for-contracts.html) | partial | 80% / 80% | 4,664 / 4,643 |

### Both documents: ScienceTech Xplore (14 pairs)

| case | page | overlap | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|
| CE-01 | [CE-01-emerging-non-volatile-memory-technologies-and-their-impact-o-vs-emerging-non-volatile-memory-technologies-and-their-impacton.html](full_copies/CE-01-emerging-non-volatile-memory-technologies-and-their-impact-o-vs-emerging-non-volatile-memory-technologies-and-their-impacton.html) | full copy | 98% / 98% | 6,082 / 6,077 |
| CE-07 | [CE-07-advanced-deep-learning-architectures-for-scalable-and-explai-vs-advanced-deep-learning-architectures-for-scalable-and-explai.html](full_copies/CE-07-advanced-deep-learning-architectures-for-scalable-and-explai-vs-advanced-deep-learning-architectures-for-scalable-and-explai.html) | full copy | 97% / 98% | 3,200 / 3,164 |
| CE-09 | [CE-09-automated-program-synthesis-and-optimization-using-foundatio-vs-automated-program-synthesis-and-optimization-using-foundatio.html](full_copies/CE-09-automated-program-synthesis-and-optimization-using-foundatio-vs-automated-program-synthesis-and-optimization-using-foundatio.html) | full copy | 99% / 99% | 5,093 / 5,076 |
| CE-09 | [CE-09-per-run-automated-program-synthesis-and-optimization-using-foundatio-vs-automated-program-synthesis-and-optimization-using-foundatio.html](full_copies/CE-09-per-run-automated-program-synthesis-and-optimization-using-foundatio-vs-automated-program-synthesis-and-optimization-using-foundatio.html) *(per-passage view)* | full copy | 99% / 99% | 5,093 / 5,076 |
| CE-10 | [CE-10-multi-objective-federated-optimization-for-decentralized-ai--vs-multi-objective-federated-optimization-for-decentralized-ai-.html](full_copies/CE-10-multi-objective-federated-optimization-for-decentralized-ai--vs-multi-objective-federated-optimization-for-decentralized-ai-.html) | full copy | 97% / 98% | 5,626 / 5,615 |
| CE-11 | [CE-11-event-driven-data-engineering-in-microservices-architectures-vs-event-driven-data-engineering-in-microservices-architectures.html](full_copies/CE-11-event-driven-data-engineering-in-microservices-architectures-vs-event-driven-data-engineering-in-microservices-architectures.html) | full copy | 97% / 97% | 4,449 / 4,439 |
| CE-12 | [CE-12-innovative-architectural-designs-for-next-generation-highper-vs-innovative-architectural-designs-for-next-generation-highper.html](full_copies/CE-12-innovative-architectural-designs-for-next-generation-highper-vs-innovative-architectural-designs-for-next-generation-highper.html) | full copy | 95% / 97% | 5,249 / 5,110 |
| CE-13 | [CE-13-hardware-software-co-design-for-performance-optimization-in--vs-hardware-software-co-design-for-performance-optimization-in-.html](full_copies/CE-13-hardware-software-co-design-for-performance-optimization-in--vs-hardware-software-co-design-for-performance-optimization-in-.html) | full copy | 96% / 97% | 3,662 / 3,630 |
| CE-14 | [CE-14-design-patterns-for-scalable-microservices-in-banking-and-in-vs-design-patterns-for-scalable-microservices-in-banking-and-in.html](full_copies/CE-14-design-patterns-for-scalable-microservices-in-banking-and-in-vs-design-patterns-for-scalable-microservices-in-banking-and-in.html) | full copy | 96% / 97% | 4,801 / 4,786 |
| CE-15 | [CE-15-the-future-of-heterogeneous-computing-integrating-cpus-gpus--vs-the-future-of-heterogeneous-computing-integrating-cpus-gpus-.html](full_copies/CE-15-the-future-of-heterogeneous-computing-integrating-cpus-gpus--vs-the-future-of-heterogeneous-computing-integrating-cpus-gpus-.html) | full copy | 98% / 98% | 7,078 / 7,108 |
| CE-16 | [CE-16-reinforcement-learning-in-dynamic-environments-challenges-an-vs-reinforcement-learning-in-dynamic-environments-challenges-an.html](full_copies/CE-16-reinforcement-learning-in-dynamic-environments-challenges-an-vs-reinforcement-learning-in-dynamic-environments-challenges-an.html) | full copy | 98% / 97% | 6,599 / 6,696 |
| CE-17 | [CE-17-blockchain-enabled-secure-data-management-in-cloud-based-hig-vs-blockchain-enabled-secure-data-management-in-cloudbased-high.html](full_copies/CE-17-blockchain-enabled-secure-data-management-in-cloud-based-hig-vs-blockchain-enabled-secure-data-management-in-cloudbased-high.html) | full copy | 98% / 97% | 6,604 / 6,642 |
| CE-18 | [CE-18-iot-and-big-data-ecosystems-a-comprehensive-review-of-techno-vs-iot-and-big-data-ecosystems-a-comprehensive-review-of-techno.html](full_copies/CE-18-iot-and-big-data-ecosystems-a-comprehensive-review-of-techno-vs-iot-and-big-data-ecosystems-a-comprehensive-review-of-techno.html) | full copy | 98% / 98% | 6,710 / 6,701 |
| CE-19 | [CE-19-ai-driven-insights-for-risk-management-in-banking-leveraging-vs-ai-driven-insights-for-risk-management-in-banking-leveraging.html](full_copies/CE-19-ai-driven-insights-for-risk-management-in-banking-leveraging-vs-ai-driven-insights-for-risk-management-in-banking-leveraging.html) | full copy | 97% / 98% | 4,894 / 4,861 |
| CE-32 | [CE-32-the-role-of-explainable-ai-in-enhancing-data-driven-decision-vs-the-role-of-explainable-ai-in-enhancing-data-driven-decision.html](full_copies/CE-32-the-role-of-explainable-ai-in-enhancing-data-driven-decision-vs-the-role-of-explainable-ai-in-enhancing-data-driven-decision.html) | full copy | 98% / 98% | 5,956 / 6,005 |

### Both documents: Springer (1 pair)

| case | page | overlap | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|
| CE-X5 | [CE-X5-an-extension-of-the-mixed-integer-part-of-a-nonlinear-form-vs-the-integer-part-of-a-nonlinear-form-with-integer-variables-74-shingle-matches.html](partial_overlap/CE-X5-an-extension-of-the-mixed-integer-part-of-a-nonlinear-form-vs-the-integer-part-of-a-nonlinear-form-with-integer-variables-74-shingle-matches.html) | partial | 47% / 40% | 2,195 / 2,643 |

### Both documents: IAEME Publication (4 pairs)

| case | page | overlap | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|
| CE-03 | [CE-03-ai-based-cloud-governance-for-multi-cloud-compliance-managem-vs-ai-driven-governance-for-multi-cloud-compliance-an-automated.html](full_copies/CE-03-ai-based-cloud-governance-for-multi-cloud-compliance-managem-vs-ai-driven-governance-for-multi-cloud-compliance-an-automated.html) | full copy | 88% / 86% | 4,146 / 4,235 |
| CE-21 | [CE-21-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html](full_copies/CE-21-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html) | full copy | 95% / 96% | 2,657 / 2,622 |
| CE-21 | [CE-21-per-run-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html](full_copies/CE-21-per-run-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html) *(per-passage view)* | full copy | 95% / 96% | 2,657 / 2,622 |
| CE-02 | [CE-02-proactive-vulnerability-management-in-cloud-clusters-through-vs-threat-intelligence-enhanced-by-ai-for-self-sustained-vulner.html](partial_overlap/CE-02-proactive-vulnerability-management-in-cloud-clusters-through-vs-threat-intelligence-enhanced-by-ai-for-self-sustained-vulner.html) | partial | 74% / 73% | 2,534 / 2,549 |
| CE-N02 | [CE-N02-electric-vehicle-ownership-in-kerala-insights-on-brand-choic-vs-electric-vehicles-in-india-bridging-the-gap-between-expectat-22-shingle-matches.html](partial_overlap/CE-N02-electric-vehicle-ownership-in-kerala-insights-on-brand-choic-vs-electric-vehicles-in-india-bridging-the-gap-between-expectat-22-shingle-matches.html) | partial | 14% / 14% | 4,613 / 4,539 |

### Both documents: the same other publisher (3 pairs)

| case | page | overlap | publisher | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|---|
| AN-03 | [AN-03-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html](full_copies/AN-03-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html) | full copy | International Journal of Pharmaceutical and Clinical Research | 92% / 93% | 2,581 / 2,504 |
| AN-03 | [AN-03-per-run-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html](full_copies/AN-03-per-run-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html) *(per-passage view)* | full copy | International Journal of Pharmaceutical and Clinical Research | 92% / 93% | 2,581 / 2,504 |
| AN-04 | [AN-04-cross-cultural-competence-in-teaching-english-vs-cross-cultural-competence-in-teaching-english.html](full_copies/AN-04-cross-cultural-competence-in-teaching-english-vs-cross-cultural-competence-in-teaching-english.html) | full copy | Zenodo | 93% / 96% | 1,205 / 1,163 |
| CE-30 | [CE-30-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html](full_copies/CE-30-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html) | full copy | Open Knowledge | 83% / 67% | 1,447 / 1,808 |
| CE-30 | [CE-30-per-run-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html](full_copies/CE-30-per-run-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html) *(per-passage view)* | full copy | Open Knowledge | 83% / 67% | 1,447 / 1,808 |

### Different publishers (14 pairs)

| case | page | overlap | publishers (A / B) | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|---|
| CE-05 | [CE-05-designing-interpretable-ml-system-to-enhance-trust-in-health-vs-interpretable-machine-learning-in-healthcare-a-systematic-re.html](full_copies/CE-05-designing-interpretable-ml-system-to-enhance-trust-in-health-vs-interpretable-machine-learning-in-healthcare-a-systematic-re.html) | full copy | arXiv / IAEME Publication | 70% / 88% | 22,792 / 17,998 |
| CE-05 | [CE-05-per-run-designing-interpretable-ml-system-to-enhance-trust-in-health-vs-interpretable-machine-learning-in-healthcare-a-systematic-re.html](full_copies/CE-05-per-run-designing-interpretable-ml-system-to-enhance-trust-in-health-vs-interpretable-machine-learning-in-healthcare-a-systematic-re.html) *(per-passage view)* | full copy | arXiv / IAEME Publication | 70% / 88% | 22,792 / 17,998 |
| CE-N09 | [CE-N09-sustainable-and-responsible-artificial-intelligence-implemen-vs-ethical-and-regenerative-ai-adoption-across-health-systems.html](full_copies/CE-N09-sustainable-and-responsible-artificial-intelligence-implemen-vs-ethical-and-regenerative-ai-adoption-across-health-systems.html) | full copy | International Journal of Innovative Science and Research Technology / European Data Science Journal | 97% / 84% | 6,516 / 7,512 |
| CE-N13 | [CE-N13-cybersecurity-awareness-on-cybercrime-among-the-youth-in-gau-vs-cybersecurity-awareness-on-cybercrime-among-the-youth-in-ind.html](full_copies/CE-N13-cybersecurity-awareness-on-cybercrime-among-the-youth-in-gau-vs-cybersecurity-awareness-on-cybercrime-among-the-youth-in-ind.html) | full copy | Multidisciplinary Center / Continuing Professional Development Events New Mumbai | 79% / 87% | 4,450 / 4,026 |
| CE-N13 | [CE-N13-per-run-cybersecurity-awareness-on-cybercrime-among-the-youth-in-gau-vs-cybersecurity-awareness-on-cybercrime-among-the-youth-in-ind.html](full_copies/CE-N13-per-run-cybersecurity-awareness-on-cybercrime-among-the-youth-in-gau-vs-cybersecurity-awareness-on-cybercrime-among-the-youth-in-ind.html) *(per-passage view)* | full copy | Multidisciplinary Center / Continuing Professional Development Events New Mumbai | 79% / 87% | 4,450 / 4,026 |
| CE-X1 | [CE-X1-autonomous-systems-challenges-and-opportunities-vs-autonomous-systems-challenges-and-opportunities.html](full_copies/CE-X1-autonomous-systems-challenges-and-opportunities-vs-autonomous-systems-challenges-and-opportunities.html) | full copy | EWA Publishing / GSC Online Press | 82% / 91% | 1,384 / 1,245 |
| AN-01 | [AN-01-fatherhood-fathering-resettlement-and-integration-a-study-of-vs-being-a-father-in-my-new-society-a-phenomenological-study-of.html](partial_overlap/AN-01-fatherhood-fathering-resettlement-and-integration-a-study-of-vs-being-a-father-in-my-new-society-a-phenomenological-study-of.html) | partial | Queensland University of Technology / McGill University | 7% / 10% | 73,347 / 49,760 |
| CE-04 | [CE-04-teachers-perspectives-on-artificial-intelligence-in-educatio-vs-balancing-innovation-and-ethics-educators-perspectives-on-th-48-shingle-matches.html](partial_overlap/CE-04-teachers-perspectives-on-artificial-intelligence-in-educatio-vs-balancing-innovation-and-ethics-educators-perspectives-on-th-48-shingle-matches.html) | partial | Syncsci Publishing Pte., Ltd. / The USA Journals | 19% / 24% | 4,768 / 3,883 |
| CE-06 | [CE-06-religious-diversity-in-the-digital-economy-interfaith-legal--vs-religious-diversity-and-the-digital-economy-legal-academic-p.html](partial_overlap/CE-06-religious-diversity-in-the-digital-economy-interfaith-legal--vs-religious-diversity-and-the-digital-economy-legal-academic-p.html) | partial | Universitas Negeri Semarang / International Research Institute Pakistan | 52% / 61% | 12,208 / 11,067 |
| CE-29 | [CE-29-smart-contracts-and-machine-learning-exploring-blockchain-an-vs-exploring-smart-contracts-and-artificial-intelligence-in-fin.html](partial_overlap/CE-29-smart-contracts-and-machine-learning-exploring-blockchain-an-vs-exploring-smart-contracts-and-artificial-intelligence-in-fin.html) | partial | Indian Society for Education and Environment / Science Research Society | 34% / 38% | 6,057 / 5,387 |
| CE-31 | [CE-31-ai-liability-and-accountability-a-review-of-emerging-legal-f-vs-legal-frameworks-for-ai-service-business-participants-a-comp-15-shingle-matches.html](partial_overlap/CE-31-ai-liability-and-accountability-a-review-of-emerging-legal-f-vs-legal-frameworks-for-ai-service-business-participants-a-comp-15-shingle-matches.html) | partial | International Journal of Science and Research / Springer | 15% / 4% | 2,877 / 9,547 |
| CE-33 | [CE-33-ai-driven-energy-optimisation-for-enterprise-integration-pla-vs-ai-driven-energy-optimisation-for-enterprise-integration-pla.html](partial_overlap/CE-33-ai-driven-energy-optimisation-for-enterprise-integration-pla-vs-ai-driven-energy-optimisation-for-enterprise-integration-pla.html) | partial | ScienceTech Xplore / Technoscience Academy | 60% / 78% | 2,494 / 1,913 |
| CE-N05 | [CE-N05-radiological-and-functional-outcome-of-medial-epicondyle-fra-vs-clinical-results-of-surgically-treated-medial-humeral-epicon-65-shingle-matches.html](partial_overlap/CE-N05-radiological-and-functional-outcome-of-medial-epicondyle-fra-vs-clinical-results-of-surgically-treated-medial-humeral-epicon-65-shingle-matches.html) | partial | Lumbini Medical College / Springer | 51% / 52% | 2,895 / 2,843 |
| CE-N14 | [CE-N14-vigilante-groups-and-policing-in-a-democratizing-nigeria-nav-vs-vigilantism-and-policing-in-akwa-ibom-state-of-nigeria-1987--55-shingle-matches.html](partial_overlap/CE-N14-vigilante-groups-and-policing-in-a-democratizing-nigeria-nav-vs-vigilantism-and-policing-in-akwa-ibom-state-of-nigeria-1987--55-shingle-matches.html) | partial | Universidade Federal do Rio Grande do Sul / Bluemark Publishers | 36% / 34% | 6,743 / 7,069 |
| CE-X2 | [CE-X2-effective-strategies-for-mitigating-bias-in-hiring-algorithm-vs-a-machine-learning-approach-to-recognize-bias-and-discrimina-22-shingle-matches.html](partial_overlap/CE-X2-effective-strategies-for-mitigating-bias-in-hiring-algorithm-vs-a-machine-learning-approach-to-recognize-bias-and-discrimina-22-shingle-matches.html) | partial | Springer / United Research Forum | 21% / 19% | 5,243 / 5,706 |
| CE-X3 | [CE-X3-internet-of-things-iot-based-smart-environment-integrating-v-vs-internet-of-things-iot-based-smart-environment-integrating-v-28-shingle-matches.html](partial_overlap/CE-X3-internet-of-things-iot-based-smart-environment-integrating-v-vs-internet-of-things-iot-based-smart-environment-integrating-v-28-shingle-matches.html) | partial | Foundation of Computer Science / South Asia Management Association | 15% / 24% | 3,734 / 2,428 |
