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
- Some pairs have two pages: the whole-document diff and a *per-passage view* that lists each matched
  passage separately. The per-passage view is the one that can show text that moved to a different
  position in the other document.

67 pages covering 62 document pairs.

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

Where a publisher has already acted publicly: 11 of the 12 papers in the `CE-N16` group were retracted
by their publisher in 2020–21, and the later document in `CE-N05` was retracted by Cureus on 2017-12-13
(retraction notice 10.7759/cureus.r9).

Documents that cite their counterpart: `CE-N14` (the later document lists the earlier one in its
references) and one pair in `CE-N16`.

## Full or near-full copies

| case | page | shared text (A / B) | length in words (A / B) |
|---|---|---|---|
| AN-03 | [AN-03-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-…](full_copies/AN-03-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html) | 92% / 93% | 2,581 / 2,504 |
| AN-04 | [AN-04-cross-cultural-competence-in-teaching-english-vs-cross-cultural-…](full_copies/AN-04-cross-cultural-competence-in-teaching-english-vs-cross-cultural-competence-in-teaching-english.html) | 93% / 96% | 1,205 / 1,163 |
| CE-01 | [CE-01-emerging-non-volatile-memory-technologies-and-their-impact-o-vs-…](full_copies/CE-01-emerging-non-volatile-memory-technologies-and-their-impact-o-vs-emerging-non-volatile-memory-technologies-and-their-impacton.html) | 98% / 98% | 6,082 / 6,077 |
| CE-03 | [CE-03-ai-based-cloud-governance-for-multi-cloud-compliance-managem-vs-…](full_copies/CE-03-ai-based-cloud-governance-for-multi-cloud-compliance-managem-vs-ai-driven-governance-for-multi-cloud-compliance-an-automated.html) | 88% / 86% | 4,146 / 4,235 |
| CE-05 | [CE-05-designing-interpretable-ml-system-to-enhance-trust-in-health-vs-…](full_copies/CE-05-designing-interpretable-ml-system-to-enhance-trust-in-health-vs-interpretable-machine-learning-in-healthcare-a-systematic-re.html) | 70% / 88% | 22,792 / 17,998 |
| CE-07 | [CE-07-advanced-deep-learning-architectures-for-scalable-and-explai-vs-…](full_copies/CE-07-advanced-deep-learning-architectures-for-scalable-and-explai-vs-advanced-deep-learning-architectures-for-scalable-and-explai.html) | 97% / 98% | 3,200 / 3,164 |
| CE-09 | [CE-09-automated-program-synthesis-and-optimization-using-foundatio-vs-…](full_copies/CE-09-automated-program-synthesis-and-optimization-using-foundatio-vs-automated-program-synthesis-and-optimization-using-foundatio.html) | 99% / 99% | 5,093 / 5,076 |
| CE-10 | [CE-10-multi-objective-federated-optimization-for-decentralized-ai--vs-…](full_copies/CE-10-multi-objective-federated-optimization-for-decentralized-ai--vs-multi-objective-federated-optimization-for-decentralized-ai-.html) | 97% / 98% | 5,626 / 5,615 |
| CE-11 | [CE-11-event-driven-data-engineering-in-microservices-architectures-vs-…](full_copies/CE-11-event-driven-data-engineering-in-microservices-architectures-vs-event-driven-data-engineering-in-microservices-architectures.html) | 97% / 97% | 4,449 / 4,439 |
| CE-12 | [CE-12-innovative-architectural-designs-for-next-generation-highper-vs-…](full_copies/CE-12-innovative-architectural-designs-for-next-generation-highper-vs-innovative-architectural-designs-for-next-generation-highper.html) | 95% / 97% | 5,249 / 5,110 |
| CE-13 | [CE-13-hardware-software-co-design-for-performance-optimization-in--vs-…](full_copies/CE-13-hardware-software-co-design-for-performance-optimization-in--vs-hardware-software-co-design-for-performance-optimization-in-.html) | 96% / 97% | 3,662 / 3,630 |
| CE-14 | [CE-14-design-patterns-for-scalable-microservices-in-banking-and-in-vs-…](full_copies/CE-14-design-patterns-for-scalable-microservices-in-banking-and-in-vs-design-patterns-for-scalable-microservices-in-banking-and-in.html) | 96% / 97% | 4,801 / 4,786 |
| CE-15 | [CE-15-the-future-of-heterogeneous-computing-integrating-cpus-gpus--vs-…](full_copies/CE-15-the-future-of-heterogeneous-computing-integrating-cpus-gpus--vs-the-future-of-heterogeneous-computing-integrating-cpus-gpus-.html) | 98% / 98% | 7,078 / 7,108 |
| CE-16 | [CE-16-reinforcement-learning-in-dynamic-environments-challenges-an-vs-…](full_copies/CE-16-reinforcement-learning-in-dynamic-environments-challenges-an-vs-reinforcement-learning-in-dynamic-environments-challenges-an.html) | 98% / 97% | 6,599 / 6,696 |
| CE-17 | [CE-17-blockchain-enabled-secure-data-management-in-cloud-based-hig-vs-…](full_copies/CE-17-blockchain-enabled-secure-data-management-in-cloud-based-hig-vs-blockchain-enabled-secure-data-management-in-cloudbased-high.html) | 98% / 97% | 6,604 / 6,642 |
| CE-18 | [CE-18-iot-and-big-data-ecosystems-a-comprehensive-review-of-techno-vs-…](full_copies/CE-18-iot-and-big-data-ecosystems-a-comprehensive-review-of-techno-vs-iot-and-big-data-ecosystems-a-comprehensive-review-of-techno.html) | 98% / 98% | 6,710 / 6,701 |
| CE-19 | [CE-19-ai-driven-insights-for-risk-management-in-banking-leveraging-vs-…](full_copies/CE-19-ai-driven-insights-for-risk-management-in-banking-leveraging-vs-ai-driven-insights-for-risk-management-in-banking-leveraging.html) | 97% / 98% | 4,894 / 4,861 |
| CE-21 | [CE-21-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-…](full_copies/CE-21-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html) | 95% / 96% | 2,657 / 2,622 |
| CE-30 | [CE-30-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-…](full_copies/CE-30-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html) | 83% / 67% | 1,447 / 1,808 |
| CE-32 | [CE-32-the-role-of-explainable-ai-in-enhancing-data-driven-decision-vs-…](full_copies/CE-32-the-role-of-explainable-ai-in-enhancing-data-driven-decision-vs-the-role-of-explainable-ai-in-enhancing-data-driven-decision.html) | 98% / 98% | 5,956 / 6,005 |
| CE-36 | [CE-36-a-novel-ensemble-deep-learning-approach-for-accurate-credit--vs-…](full_copies/CE-36-a-novel-ensemble-deep-learning-approach-for-accurate-credit--vs-hybrid-ensemble-and-deep-learning-approach-for-improved-cred.html) | 96% / 93% | 4,839 / 4,982 |
| CE-37 | [37-140649-140665-o-insight-system-a-multi-agent-ai-platform-for-automa…](full_copies/CE-37-insight-system/37-140649-140665-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html) | 96% / 91% | 5,765 / 6,027 |
| CE-37 | [37-140649-140684-o-insight-system-a-multi-agent-ai-platform-for-automa…](full_copies/CE-37-insight-system/37-140649-140684-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html) | 94% / 91% | 5,765 / 5,980 |
| CE-37 | [37-140649-140684-per-run-o-insight-system-a-multi-agent-ai-platform-fo…](full_copies/CE-37-insight-system/37-140649-140684-per-run-o-insight-system-a-multi-agent-ai-platform-for-automated-iee-vs-oretes-insight-system-a-multi-agent-ai-platform-for-automate.html) *(per-passage view)* | 94% / 91% | 5,765 / 5,980 |
| CE-38 | [38-140562-140693-design-and-implementation-of-an-ai-based-vulnerabilit…](full_copies/CE-38-gift-zestera-student-projects/38-140562-140693-design-and-implementation-of-an-ai-based-vulnerability-manag-vs-ai-powered-regulatory-compliance-checker-for-contracts.html) | 81% / 80% | 4,614 / 4,643 |
| CE-38 | [38-140564-140604-study-planner-app-vs-ai-study-habit-analyzer.html](full_copies/CE-38-gift-zestera-student-projects/38-140564-140604-study-planner-app-vs-ai-study-habit-analyzer.html) | 91% / 92% | 4,316 / 4,251 |
| CE-38 | [38-140581-140679-agrisahayak-ai-based-smart-agriculture-management-das…](full_copies/CE-38-gift-zestera-student-projects/38-140581-140679-agrisahayak-ai-based-smart-agriculture-management-dashboard--vs-agrisahayak-ai-based-digital-agriculture-assistant.html) | 93% / 92% | 3,873 / 3,888 |
| CE-38 | [38-140601-140657-debeats-full-stack-food-delivery-application-vs-busbe…](full_copies/CE-38-gift-zestera-student-projects/38-140601-140657-debeats-full-stack-food-delivery-application-vs-busbee-real-time-school-bus-monitoring-system.html) | 84% / 80% | 4,562 / 4,821 |
| CE-N09 | [CE-N09-sustainable-and-responsible-artificial-intelligence-implemen-vs…](full_copies/CE-N09-sustainable-and-responsible-artificial-intelligence-implemen-vs-ethical-and-regenerative-ai-adoption-across-health-systems-37-shingle-matches.html) | 97% / 84% | 6,516 / 7,512 |
| CE-N13 | [CE-N13-cybersecurity-awareness-on-cybercrime-among-the-youth-in-gau-vs…](full_copies/CE-N13-cybersecurity-awareness-on-cybercrime-among-the-youth-in-gau-vs-cybersecurity-awareness-on-cybercrime-among-the-youth-in-ind-68-shingle-matches.html) | 79% / 87% | 4,450 / 4,026 |
| CE-N16 | [solutions-of-the-equilibrium-equations-with-finite-mass-subj-vs-bounda…](full_copies/CE-N16-nonlinear-form-and-schrodinger-inequalities/solutions-of-the-equilibrium-equations-with-finite-mass-subj-vs-boundary-value-behaviors-for-solutions-of-the-equilibrium-eq-44-shingle-matches.html) | 80% / 77% | 2,508 / 2,598 |
| CE-X1 | [CE-X1-autonomous-systems-challenges-and-opportunities-vs-autonomous-sy…](full_copies/CE-X1-autonomous-systems-challenges-and-opportunities-vs-autonomous-systems-challenges-and-opportunities-23-shingle-matches.html) | 82% / 91% | 1,384 / 1,245 |

## Partial overlap

| case | page | shared text (A / B) | length in words (A / B) |
|---|---|---|---|
| AN-01 | [AN-01-fatherhood-fathering-resettlement-and-integration-a-study-of-vs-…](partial_overlap/AN-01-fatherhood-fathering-resettlement-and-integration-a-study-of-vs-being-a-father-in-my-new-society-a-phenomenological-study-of.html) | 7% / 10% | 73,347 / 49,760 |
| CE-02 | [CE-02-proactive-vulnerability-management-in-cloud-clusters-through-vs-…](partial_overlap/CE-02-proactive-vulnerability-management-in-cloud-clusters-through-vs-threat-intelligence-enhanced-by-ai-for-self-sustained-vulner.html) | 74% / 73% | 2,534 / 2,549 |
| CE-04 | [CE-04-teachers-perspectives-on-artificial-intelligence-in-educatio-vs-…](partial_overlap/CE-04-teachers-perspectives-on-artificial-intelligence-in-educatio-vs-balancing-innovation-and-ethics-educators-perspectives-on-th-48-shingle-matches.html) | 19% / 24% | 4,768 / 3,883 |
| CE-06 | [CE-06-religious-diversity-in-the-digital-economy-interfaith-legal--vs-…](partial_overlap/CE-06-religious-diversity-in-the-digital-economy-interfaith-legal--vs-religious-diversity-and-the-digital-economy-legal-academic-p.html) | 52% / 61% | 12,208 / 11,067 |
| CE-29 | [CE-29-smart-contracts-and-machine-learning-exploring-blockchain-an-vs-…](partial_overlap/CE-29-smart-contracts-and-machine-learning-exploring-blockchain-an-vs-exploring-smart-contracts-and-artificial-intelligence-in-fin.html) | 34% / 38% | 6,057 / 5,387 |
| CE-31 | [CE-31-ai-liability-and-accountability-a-review-of-emerging-legal-f-vs-…](partial_overlap/CE-31-ai-liability-and-accountability-a-review-of-emerging-legal-f-vs-legal-frameworks-for-ai-service-business-participants-a-comp-15-shingle-matches.html) | 15% / 4% | 2,877 / 9,547 |
| CE-33 | [CE-33-ai-driven-energy-optimisation-for-enterprise-integration-pla-vs-…](partial_overlap/CE-33-ai-driven-energy-optimisation-for-enterprise-integration-pla-vs-ai-driven-energy-optimisation-for-enterprise-integration-pla.html) | 60% / 78% | 2,494 / 1,913 |
| CE-34 | [34-advanced-agricultural-decision-system-using-recurrent-polyno-vs-adv…](partial_overlap/CE-34-agricultural-decision-system/34-advanced-agricultural-decision-system-using-recurrent-polyno-vs-advanced-agricultural-decision-system-using-recurrent-polyno.html) | 58% / 55% | 3,277 / 3,472 |
| CE-34 | [34b-140288-140417-a-data-driven-sound-analysis-approach-for-detecting-…](partial_overlap/CE-34-agricultural-decision-system/34b-140288-140417-a-data-driven-sound-analysis-approach-for-detecting-mechanic-vs-transformer-driven-wavlm-audio-modelling-for-robust-predicti.html) | 59% / 50% | 3,344 / 4,007 |
| CE-35 | [CE-35-advanced-ai-facing-voting-system-vs-advanced-ai-facing-voting-sy…](partial_overlap/CE-35-advanced-ai-facing-voting-system-vs-advanced-ai-facing-voting-system.html) | 74% / 60% | 3,115 / 3,834 |
| CE-38 | [38-140555-140566-ai-doctor-voice-amp-vision-vs-realestate-houseprice-p…](partial_overlap/CE-38-gift-zestera-student-projects/38-140555-140566-ai-doctor-voice-amp-vision-vs-realestate-houseprice-prediction.html) | 63% / 62% | 3,861 / 3,940 |
| CE-38 | [38-140555-140566-per-run-ai-doctor-voice-amp-vision-vs-realestate-hous…](partial_overlap/CE-38-gift-zestera-student-projects/38-140555-140566-per-run-ai-doctor-voice-amp-vision-vs-realestate-houseprice-prediction.html) *(per-passage view)* | 63% / 62% | 3,861 / 3,940 |
| CE-38 | [38-140567-140578-per-run-student-study-portal-vs-automated-examination…](partial_overlap/CE-38-gift-zestera-student-projects/38-140567-140578-per-run-student-study-portal-vs-automated-examination-seating-arrangement-and-hall-allocatio.html) *(per-passage view)* | 61% / 66% | 3,454 / 3,273 |
| CE-38 | [38-140567-140578-student-study-portal-vs-automated-examination-seating…](partial_overlap/CE-38-gift-zestera-student-projects/38-140567-140578-student-study-portal-vs-automated-examination-seating-arrangement-and-hall-allocatio.html) | 61% / 66% | 3,454 / 3,273 |
| CE-38 | [38-140602-140673-design-and-implementation-of-smart-mart-an-ai-powered…](partial_overlap/CE-38-gift-zestera-student-projects/38-140602-140673-design-and-implementation-of-smart-mart-an-ai-powered-e-comm-vs-design-and-implementation-of-home-deal-a-web-based-house-ren.html) | 45% / 40% | 4,922 / 5,164 |
| CE-38 | [38-140602-140673-per-run-design-and-implementation-of-smart-mart-an-ai…](partial_overlap/CE-38-gift-zestera-student-projects/38-140602-140673-per-run-design-and-implementation-of-smart-mart-an-ai-powered-e-comm-vs-design-and-implementation-of-home-deal-a-web-based-house-ren.html) *(per-passage view)* | 45% / 40% | 4,922 / 5,164 |
| CE-38 | [38-140606-140637-hospital-managemnet-system-vs-the-women-safety-tracke…](partial_overlap/CE-38-gift-zestera-student-projects/38-140606-140637-hospital-managemnet-system-vs-the-women-safety-tracker-app.html) | 75% / 77% | 4,307 / 4,212 |
| CE-38 | [38-140618-140657-per-run-smart-inventory-and-product-management-system…](partial_overlap/CE-38-gift-zestera-student-projects/38-140618-140657-per-run-smart-inventory-and-product-management-system-vs-busbee-real-time-school-bus-monitoring-system.html) *(per-passage view)* | 31% / 34% | 5,320 / 4,821 |
| CE-38 | [38-140618-140657-smart-inventory-and-product-management-system-vs-busb…](partial_overlap/CE-38-gift-zestera-student-projects/38-140618-140657-smart-inventory-and-product-management-system-vs-busbee-real-time-school-bus-monitoring-system.html) | 31% / 34% | 5,320 / 4,821 |
| CE-38 | [38-140627-140693-design-and-implementation-of-an-ai-based-vulnerabilit…](partial_overlap/CE-38-gift-zestera-student-projects/38-140627-140693-design-and-implementation-of-an-ai-based-vulnerability-manag-vs-ai-powered-regulatory-compliance-checker-for-contracts.html) | 80% / 80% | 4,664 / 4,643 |
| CE-N02 | [CE-N02-electric-vehicle-ownership-in-kerala-insights-on-brand-choic-vs…](partial_overlap/CE-N02-electric-vehicle-ownership-in-kerala-insights-on-brand-choic-vs-electric-vehicles-in-india-bridging-the-gap-between-expectat-22-shingle-matches.html) | 14% / 14% | 4,613 / 4,539 |
| CE-N05 | [CE-N05-radiological-and-functional-outcome-of-medial-epicondyle-fra-vs…](partial_overlap/CE-N05-radiological-and-functional-outcome-of-medial-epicondyle-fra-vs-clinical-results-of-surgically-treated-medial-humeral-epicon-65-shingle-matches.html) | 51% / 52% | 2,895 / 2,843 |
| CE-N14 | [CE-N14-vigilante-groups-and-policing-in-a-democratizing-nigeria-nav-vs…](partial_overlap/CE-N14-vigilante-groups-and-policing-in-a-democratizing-nigeria-nav-vs-vigilantism-and-policing-in-akwa-ibom-state-of-nigeria-1987--55-shingle-matches.html) | 36% / 34% | 6,743 / 7,069 |
| CE-N16 | [a-sharp-trudinger-type-inequality-for-harmonic-functions-and-vs-new-ri…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/a-sharp-trudinger-type-inequality-for-harmonic-functions-and-vs-new-riesz-representations-of-linear-maps-associated-with-cer-49-shingle-matches.html) | 49% / 50% | 2,969 / 2,981 |
| CE-X5 | [an-extension-of-the-mixed-integer-part-of-a-nonlinear-form-vs-the-inte…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/an-extension-of-the-mixed-integer-part-of-a-nonlinear-form-vs-the-integer-part-of-a-nonlinear-form-with-integer-variables-74-shingle-matches.html) | 47% / 40% | 2,195 / 2,643 |
| CE-N16 | [levin-s-type-boundary-behaviors-for-functions-harmonic-and-a-vs-retrac…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/levin-s-type-boundary-behaviors-for-functions-harmonic-and-a-vs-retracted-article-matsaev-type-inequalities-on-smooth-cones-47-shingle-matches.html) | 80% / 71% | 2,116 / 2,394 |
| CE-N16 | [poisson-type-inequalities-for-growth-properties-of-positive--vs-new-ap…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/poisson-type-inequalities-for-growth-properties-of-positive--vs-new-applications-of-schr-dingerean-green-potential-to-bounda-67-shingle-matches.html) | 53% / 53% | 3,018 / 3,116 |
| CE-N16 | [proofs-to-one-inequality-conjecture-for-the-non-integer-part-vs-an-ext…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/proofs-to-one-inequality-conjecture-for-the-non-integer-part-vs-an-extension-of-the-mixed-integer-part-of-a-nonlinear-form-62-shingle-matches.html) | 69% / 67% | 2,516 / 2,643 |
| CE-N16 | [proofs-to-one-inequality-conjecture-for-the-non-integer-part-vs-the-in…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/proofs-to-one-inequality-conjecture-for-the-non-integer-part-vs-the-integer-part-of-a-nonlinear-form-with-integer-variables-29-shingle-matches.html) | 23% / 20% | 2,195 / 2,516 |
| CE-N16 | [sharp-geometrical-properties-of-a-rarefied-sets-via-fixed-po-vs-fixed-…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/sharp-geometrical-properties-of-a-rarefied-sets-via-fixed-po-vs-fixed-point-theorems-for-solutions-of-the-stationary-schr-di-37-shingle-matches.html) | 35% / 32% | 3,055 / 3,414 |
| CE-N16 | [solutions-of-the-dirichlet-schr-dinger-problems-with-continu-vs-an-app…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/solutions-of-the-dirichlet-schr-dinger-problems-with-continu-vs-an-application-of-the-inequality-for-modified-poisson-kernel-20-shingle-matches.html) | 21% / 25% | 2,331 / 2,007 |
| CE-N16 | [stability-and-direction-for-a-class-of-schr-dingerean-differ-vs-retrac…](partial_overlap/CE-N16-nonlinear-form-and-schrodinger-inequalities/stability-and-direction-for-a-class-of-schr-dingerean-differ-vs-retracted-article-new-applications-of-schr-dinger-type-inequ-14-shingle-matches.html) | 18% / 13% | 2,235 / 3,181 |
| CE-X2 | [CE-X2-effective-strategies-for-mitigating-bias-in-hiring-algorithm-vs-…](partial_overlap/CE-X2-effective-strategies-for-mitigating-bias-in-hiring-algorithm-vs-a-machine-learning-approach-to-recognize-bias-and-discrimina-22-shingle-matches.html) | 21% / 19% | 5,243 / 5,706 |
| CE-X3 | [CE-X3-internet-of-things-iot-based-smart-environment-integrating-v-vs-…](partial_overlap/CE-X3-internet-of-things-iot-based-smart-environment-integrating-v-vs-internet-of-things-iot-based-smart-environment-integrating-v-28-shingle-matches.html) | 15% / 24% | 3,734 / 2,428 |
| CE-X4 | [CE-X4-the-role-of-blockchain-technology-in-enhancing-financial-sec-vs-…](partial_overlap/CE-X4-the-role-of-blockchain-technology-in-enhancing-financial-sec-vs-enhancing-data-security-in-financial-institutions-with-block-18-shingle-matches.html) | 4% / 4% | 5,367 / 5,354 |
