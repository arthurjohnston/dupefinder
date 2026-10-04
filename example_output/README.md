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
- Pages whose case label starts `LEAD-` were added on 2026-10-04 from a later sweep of this project's
  unreviewed candidates. Their bylines were read from the PDFs and they went through the checks below;
  the label is the project's own numbering for pairs still being looked at.


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

## Reworded and "spun" text

Some pairs share text that has been reworded rather than repeated. The most recognisable kind is
**article spinning**: software replaces words with dictionary synonyms one at a time and leaves the
sentence structure alone, so the result is grammatical but often odd in context. A sentence such as
"the benefit of PCA is to reduce the dimension of the data" comes out as "the profit of PCA is reduce
dimensionality of the data". Titles get the same treatment: "Anticipated Security Model for Session
Transfer" becomes "Probable Defense Representation for Session Transfer".
Lighter rewording, by a person or a paraphrasing tool, does the same thing less mechanically:
"furnish an alternative to the conventional" becomes "provide a replacement for the traditional".

**Why it matters for the figures.** The overlap figures on this site count runs of 10 consecutive
matching words, allowing an occasional substituted word inside a run. Spinning breaks text into short
pieces, so the 10-word measurement can be very low even when almost every sentence of one document has
a reworded counterpart in the other. Measured with 5-word runs and a wider allowance for substituted
words, the same pairs show several times as much shared text. Where a page below was rendered with
those looser settings, its footer says so.

**What it looks like on a page.** In the word-level diff, a spun passage is a sentence that matches
almost word for word, with single words marked as changed every few words. The changed words are
usually synonyms of the originals rather than new content.

Pages on this site that show reworded or spun text:

- `LEAD-11` ([page](full_copies/LEAD-11-ijsrcseit-2019/LEAD-11-139195-139329-anticipated-security-model-for-session-transfer-and-services-vs-probable-defense-representation-for-session-transfer-and-net.html), [page](partial_overlap/LEAD-11-ijsrcseit-2019/LEAD-11-139756-139842-sentiment-analysis-for-product-recommendation-system-using-h-vs-sentiment-analysis-for-product-recommendation-system-using-e.html)): student papers in one journal with
  synonym-swapped titles ("Anticipated Security Model" / "Probable Defense Representation") and
  machine-spun phrasing in the body.
- `LEAD-15e` ([page](partial_overlap/LEAD-15e-explainable-ai-for-cloud-based-machine-learning-interpretabl-vs-transparency-and-interpretability-in-cloudbased-machine-lear.html)): section headings and sentences
  reworded one for one, as a paraphrasing tool would; the 10-word measurement shows only 7% / 9%.
- `LEAD-20` ([page](partial_overlap/LEAD-20-attendance-management-system-using-facial-recognition-vs-attendance-management-system-based-on-facial-recognition.html)): a paper reworded sentence by sentence,
  keeping the original's misspelled heading "MEHODOLOGY".

## Pairs where the shared material is data

In most pairs the shared material is prose. In these five it also includes measured or surveyed
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
- **LEAD-17, distal humerus fractures** ([page](partial_overlap/LEAD-17-surgical-management-of-intercondylar-fracture-of-distal-hume-vs-surgical-management-of-distal-humeral-fractures-in-adults.html)). Two 2016 articles in the same issue of
  the *International Journal of Orthopaedics Sciences* (vol. 2, issue 4, pp. 143-145 and 375-377), with
  different authors at different colleges in Karnataka. Besides the introduction, they share the results:
  the same breakdown of fracture types and outcomes ("5 cases were of type II out of which 3 had good and
  2 fair results. There were 12 cases of type III fractures ...") and the same complications.
- **LEAD-12, ten pairs of 2026 articles** (for example the [bone-fracture pair](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140306-140451-deit-based-feature-extraction-with-ensemble-machine-learning-vs-a-dual-model-interpretable-pipeline-for-multi-class-bone-fra.html); all ten are listed under Zestera Publications below). Ten pairs of
  2026 articles in Zestera Publications journals, each pair with different author lists. In several pairs the
  shared text includes the results: the same model-comparison tables to two or four decimal places, for
  example the bone-fracture pair (RC 71.87%, PAC 73.5%, NCC 18.5%, FIGS 96.41%) and the IoT-healthcare pair
  (Ridge 82.6214, LDEC 99.0850), and in the EEG pair the same class counts for the dataset (11,340 records,
  3,192 mood disorder cases).

## Grouped by publisher

Pairs are grouped by where the two documents were published, taken from each DOI's registration
record (Crossref, or DataCite for repository and preprint deposits). "Full copy" pages are in
`full_copies/` and "partial" pages in `partial_overlap/`, as described above.

### Both documents: Zestera Publications (27 pairs)

Journals (10), with the number of articles on this site from each (one article comes from a pair listed under "Different publishers"):

- *American Journal of Management and IOT Medical Computing*: 14
- *American Journal of AI Cyber Computing Management*: 10
- *International Journal of AI Electronics and Nexus Energy*: 6
- *International Journal of Data Science and IoT Management System*: 6
- *International Journal of AI EBioMedicine Innovations*: 4
- *International Journal of AI Electrical Civil and Mechanical engineering*: 3
- *International Journal of Economic Social Science and Management LAW*: 3
- *International Journal of LAW, Arts and Humanities*: 2
- *International Journal of Pharmacy with Medical Sciences*: 2
- *American Journal of AI Digital Transformation and Regenerative Pharmacist*: 1

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
| LEAD-15c | [LEAD-15c-a-modern-learning-management-system-for-smart-vs-a-modern-web-based-e-learning-platform-for-online-education.html](full_copies/LEAD-15c-a-modern-learning-management-system-for-smart-vs-a-modern-web-based-e-learning-platform-for-online-education.html) | full copy | 96% / 98% | 1,951 / 1,861 |
| LEAD-15c | [LEAD-15c-per-run-a-modern-learning-management-system-for-smart-vs-a-modern-web-based-e-learning-platform-for-online-education.html](full_copies/LEAD-15c-per-run-a-modern-learning-management-system-for-smart-vs-a-modern-web-based-e-learning-platform-for-online-education.html) *(per-passage view)* | full copy | 96% / 98% | 1,951 / 1,861 |
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
| LEAD-12 | [LEAD-12-140295-140440-unsupervised-deep-characterization-of-machine-behavior-throu-vs-leveraging-transformed-based-speech-representation-for-indus.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140295-140440-unsupervised-deep-characterization-of-machine-behavior-throu-vs-leveraging-transformed-based-speech-representation-for-indus.html) | partial | 71% / 70% | 3,847 / 3,903 |
| LEAD-12 | [LEAD-12-140317-140463-intelligent-context-fusion-for-early-anomaly-detection-in-io-vs-detecting-anomalous-patterns-in-iot-healthcare-systems-throu.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140317-140463-intelligent-context-fusion-for-early-anomaly-detection-in-io-vs-detecting-anomalous-patterns-in-iot-healthcare-systems-throu.html) | partial | 73% / 63% | 2,791 / 3,275 |
| LEAD-12 | [LEAD-12-140318-140420-svetnet-a-deep-feature-integrated-hybrid-framework-for-hiera-vs-tri-category-medicinal-plant-classification-using-fine-grain.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140318-140420-svetnet-a-deep-feature-integrated-hybrid-framework-for-hiera-vs-tri-category-medicinal-plant-classification-using-fine-grain.html) | partial | 71% / 60% | 4,280 / 4,786 |
| LEAD-12 | [LEAD-12-140306-140451-deit-based-feature-extraction-with-ensemble-machine-learning-vs-a-dual-model-interpretable-pipeline-for-multi-class-bone-fra.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140306-140451-deit-based-feature-extraction-with-ensemble-machine-learning-vs-a-dual-model-interpretable-pipeline-for-multi-class-bone-fra.html) | partial | 70% / 55% | 3,134 / 3,797 |
| LEAD-12 | [LEAD-12-140338-140444-synaptiq-a-hybrid-neuro-analytical-framework-for-multidimens-vs-automated-multiclass-eeg-signal-classification-for-early-dia.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140338-140444-synaptiq-a-hybrid-neuro-analytical-framework-for-multidimens-vs-automated-multiclass-eeg-signal-classification-for-early-dia.html) | partial | 61% / 64% | 2,595 / 2,527 |
| LEAD-12 | [LEAD-12-140202-140330-a-scalable-ai-driven-framework-for-cybersecurity-training-si-vs-next-gen-ai-cybersecurity-simulator-with-real-time-network-i.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140202-140330-a-scalable-ai-driven-framework-for-cybersecurity-training-si-vs-next-gen-ai-cybersecurity-simulator-with-real-time-network-i.html) | partial | 61% / 60% | 2,542 / 2,573 |
| LEAD-12 | [LEAD-12-140454-140741-structured-representation-learning-of-multi-class-gait-dynam-vs-adaptive-latent-motion-intelligence-for-multi-gait-behaviora.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140454-140741-structured-representation-learning-of-multi-class-gait-dynam-vs-adaptive-latent-motion-intelligence-for-multi-gait-behaviora.html) | partial | 60% / 53% | 3,625 / 3,951 |
| LEAD-12 | [LEAD-12-140414-140429-a-next-generation-iot-driven-robotic-rescue-paradigm-for-bor-vs-torquemax-rs-an-iot-synchronized-deep-shaft-robotic-extracti.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140414-140429-a-next-generation-iot-driven-robotic-rescue-paradigm-for-bor-vs-torquemax-rs-an-iot-synchronized-deep-shaft-robotic-extracti.html) | partial | 61% / 64% | 2,945 / 2,816 |
| LEAD-12 | [LEAD-12-140259-140318-svetnet-a-novel-explainable-deep-learning-approach-for-medic-vs-svetnet-a-deep-feature-integrated-hybrid-framework-for-hiera.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140259-140318-svetnet-a-novel-explainable-deep-learning-approach-for-medic-vs-svetnet-a-deep-feature-integrated-hybrid-framework-for-hiera.html) | partial | 26% / 23% | 3,656 / 4,280 |
| LEAD-12 | [LEAD-12-140294-140468-multi-feature-wavelet-based-network-intrusion-detection-usin-vs-scalable-ai-framework-for-real-time-anomaly-detection-and-au.html](partial_overlap/LEAD-12-telangana-zestera/LEAD-12-140294-140468-multi-feature-wavelet-based-network-intrusion-detection-usin-vs-scalable-ai-framework-for-real-time-anomaly-detection-and-au.html) | partial | 46% / 39% | 2,927 / 3,449 |

### Both documents: ScienceTech Xplore (16 pairs)

Journals (5), with the number of articles on this site from each (one article comes from a pair listed under "Different publishers"):

- *International Journal of Artificial Intelligence, Data Science, and Machine Learning*: 8
- *International Journal of AI, BigData, Computational and Management Studies*: 7
- *International Journal of Emerging Trends in Computer Science and Information Technology*: 6
- *International Journal of Emerging Research in Engineering and Technology*: 6
- *American International Journal of Computer Science and Technology*: 6

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
| LEAD-13a | [LEAD-13a-how-citizen-developers-changed-the-game-vs-agentic-ai-building-self-directed-software-agents-with-multi.html](partial_overlap/LEAD-13a-how-citizen-developers-changed-the-game-vs-agentic-ai-building-self-directed-software-agents-with-multi.html) | partial | 6% / 6% | 6,224 / 6,049 |
| LEAD-13b | [LEAD-13b-ai-driven-cybersecurity-a-reinforcement-learningbased-approa-vs-an-efficient-transformer-based-model-for-automated-code-gene.html](partial_overlap/LEAD-13b-ai-driven-cybersecurity-a-reinforcement-learningbased-approa-vs-an-efficient-transformer-based-model-for-automated-code-gene.html) | partial | 6% / 6% | 5,550 / 5,980 |

### Both documents: Springer (2 pairs)

| case | page | overlap | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|
| CE-X5 | [CE-X5-an-extension-of-the-mixed-integer-part-of-a-nonlinear-form-vs-the-integer-part-of-a-nonlinear-form-with-integer-variables-74-shingle-matches.html](partial_overlap/CE-X5-an-extension-of-the-mixed-integer-part-of-a-nonlinear-form-vs-the-integer-part-of-a-nonlinear-form-with-integer-variables-74-shingle-matches.html) | partial | 47% / 40% | 2,195 / 2,643 |
| LEAD-01 | [LEAD-01-error-analysis-for-l-q-l-q-coefficient-regularized-moving-le-vs-minimal-thinness-with-respect-to-the-schr-dinger-operator-an.html](partial_overlap/LEAD-01-error-analysis-for-l-q-l-q-coefficient-regularized-moving-le-vs-minimal-thinness-with-respect-to-the-schr-dinger-operator-an.html) | partial | 6% / 9% | 4,217 / 2,885 |

### Both documents: IAEME Publication (4 pairs)

Journals (8), with the number of articles on this site from each (one article comes from a pair listed under "Different publishers"):

- *International Journal of Cloud Computing*: 2
- *International Journal of Artificial Intelligence & Machine Learning*: 1
- *International Journal of Artificial Intelligence Research and Development*: 1
- *International Journal of Artificial Intelligence and Deep Learning*: 1
- *International Journal of Commerce and Business Studies*: 1
- *International Journal of Marketing and Human Resource Management*: 1
- *International Journal of Research in Computer Applications and Information Technology*: 1
- *International Journal of Scientific Research in Computer Science and Information Technology*: 1

| case | page | overlap | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|
| CE-03 | [CE-03-ai-based-cloud-governance-for-multi-cloud-compliance-managem-vs-ai-driven-governance-for-multi-cloud-compliance-an-automated.html](full_copies/CE-03-ai-based-cloud-governance-for-multi-cloud-compliance-managem-vs-ai-driven-governance-for-multi-cloud-compliance-an-automated.html) | full copy | 88% / 86% | 4,146 / 4,235 |
| CE-21 | [CE-21-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html](full_copies/CE-21-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html) | full copy | 95% / 96% | 2,657 / 2,622 |
| CE-21 | [CE-21-per-run-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html](full_copies/CE-21-per-run-feature-selection-and-deep-learning-model-for-air-quality-pr-vs-feature-selection-and-deep-learning-model-for-air-quality-pr.html) *(per-passage view)* | full copy | 95% / 96% | 2,657 / 2,622 |
| CE-02 | [CE-02-proactive-vulnerability-management-in-cloud-clusters-through-vs-threat-intelligence-enhanced-by-ai-for-self-sustained-vulner.html](partial_overlap/CE-02-proactive-vulnerability-management-in-cloud-clusters-through-vs-threat-intelligence-enhanced-by-ai-for-self-sustained-vulner.html) | partial | 74% / 73% | 2,534 / 2,549 |
| CE-N02 | [CE-N02-electric-vehicle-ownership-in-kerala-insights-on-brand-choic-vs-electric-vehicles-in-india-bridging-the-gap-between-expectat-22-shingle-matches.html](partial_overlap/CE-N02-electric-vehicle-ownership-in-kerala-insights-on-brand-choic-vs-electric-vehicles-in-india-bridging-the-gap-between-expectat-22-shingle-matches.html) | partial | 14% / 14% | 4,613 / 4,539 |

### Both documents: Technoscience Academy (5 pairs)

Journals (3), with the number of articles on this site from each (one article comes from a pair listed under "Different publishers"):

- *International Journal of Scientific Research in Computer Science, Engineering and Information Technology*: 7
- *International Journal of Scientific Research in Science, Engineering and Technology*: 3
- *International Journal of Scientific Research in Science and Technology*: 1

| case | page | overlap | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|
| LEAD-11 | [LEAD-11-139195-139329-anticipated-security-model-for-session-transfer-and-services-vs-probable-defense-representation-for-session-transfer-and-net.html](full_copies/LEAD-11-ijsrcseit-2019/LEAD-11-139195-139329-anticipated-security-model-for-session-transfer-and-services-vs-probable-defense-representation-for-session-transfer-and-net.html) | full copy | 90% / 84% | 2,214 / 2,403 |
| LEAD-11 | [LEAD-11-139138-139532-enhanced-classification-of-incomplete-pattern-using-fuzzy-sy-vs-enhanced-classification-of-incomplete-pattern-using-hierarch.html](full_copies/LEAD-11-ijsrcseit-2019/LEAD-11-139138-139532-enhanced-classification-of-incomplete-pattern-using-fuzzy-sy-vs-enhanced-classification-of-incomplete-pattern-using-hierarch.html) | full copy | 56% / 92% | 3,190 / 1,933 |
| LEAD-11 | [LEAD-11-139756-139842-sentiment-analysis-for-product-recommendation-system-using-h-vs-sentiment-analysis-for-product-recommendation-system-using-e.html](partial_overlap/LEAD-11-ijsrcseit-2019/LEAD-11-139756-139842-sentiment-analysis-for-product-recommendation-system-using-h-vs-sentiment-analysis-for-product-recommendation-system-using-e.html) | partial | 49% / 20% | 1,417 / 3,415 |
| LEAD-11 | [LEAD-11-139583-139884-img-shelter-privacy-protection-of-images-in-online-social-ne-vs-medical-image-privacy-using-watermarking-techniques.html](partial_overlap/LEAD-11-ijsrcseit-2019/LEAD-11-139583-139884-img-shelter-privacy-protection-of-images-in-online-social-ne-vs-medical-image-privacy-using-watermarking-techniques.html) | partial | 10% / 9% | 5,496 / 6,358 |
| LEAD-11 | [LEAD-11-139373-139604-storage-and-security-preservation-using-cloud-based-intellig-vs-storage-preservation-using-big-data-based-intelligent-compre.html](partial_overlap/LEAD-11-ijsrcseit-2019/LEAD-11-139373-139604-storage-and-security-preservation-using-cloud-based-intellig-vs-storage-preservation-using-big-data-based-intelligent-compre.html) | partial | 19% / 19% | 3,789 / 3,801 |

### Both documents: the same other publisher (9 pairs)

| case | page | overlap | publisher | shared text (A / B) | length in words (A / B) |
|---|---|---|---|---|---|
| AN-03 | [AN-03-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html](full_copies/AN-03-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html) | full copy | International Journal of Pharmaceutical and Clinical Research | 92% / 93% | 2,581 / 2,504 |
| AN-03 | [AN-03-per-run-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html](full_copies/AN-03-per-run-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary--vs-a-study-of-factors-affecting-low-birth-weight-in-a-tertiary-.html) *(per-passage view)* | full copy | International Journal of Pharmaceutical and Clinical Research | 92% / 93% | 2,581 / 2,504 |
| AN-04 | [AN-04-cross-cultural-competence-in-teaching-english-vs-cross-cultural-competence-in-teaching-english.html](full_copies/AN-04-cross-cultural-competence-in-teaching-english-vs-cross-cultural-competence-in-teaching-english.html) | full copy | Zenodo | 93% / 96% | 1,205 / 1,163 |
| CE-30 | [CE-30-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html](full_copies/CE-30-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html) | full copy | Open Knowledge | 83% / 67% | 1,447 / 1,808 |
| CE-30 | [CE-30-per-run-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html](full_copies/CE-30-per-run-ai-in-healthcare-transforming-patient-care-through-predictiv-vs-ai-in-healthcare-revolutionizing-patient-care-with-predictiv.html) *(per-passage view)* | full copy | Open Knowledge | 83% / 67% | 1,447 / 1,808 |
| LEAD-05 | [LEAD-05-the-impact-of-social-class-on-language-use-in-multilingual-c-vs-the-impact-of-social-class-on-language-use-in-multilingual-c.html](full_copies/LEAD-05-the-impact-of-social-class-on-language-use-in-multilingual-c-vs-the-impact-of-social-class-on-language-use-in-multilingual-c.html) | full copy | ISRG Publishers | 68% / 91% | 3,136 / 2,329 |
| LEAD-05 | [LEAD-05-per-run-the-impact-of-social-class-on-language-use-in-multilingual-c-vs-the-impact-of-social-class-on-language-use-in-multilingual-c.html](full_copies/LEAD-05-per-run-the-impact-of-social-class-on-language-use-in-multilingual-c-vs-the-impact-of-social-class-on-language-use-in-multilingual-c.html) *(per-passage view)* | full copy | ISRG Publishers | 68% / 91% | 3,136 / 2,329 |
| LEAD-06 | [LEAD-06-artificial-intelligence-and-the-future-of-human-rights-legal-vs-lt-b-gt-artificial-intelligence-and-algorithmic-accountabili.html](full_copies/LEAD-06-artificial-intelligence-and-the-future-of-human-rights-legal-vs-lt-b-gt-artificial-intelligence-and-algorithmic-accountabili.html) | full copy | International Research Institute Pakistan | 97% / 94% | 8,758 / 9,037 |
| LEAD-06 | [LEAD-06-per-run-artificial-intelligence-and-the-future-of-human-rights-legal-vs-lt-b-gt-artificial-intelligence-and-algorithmic-accountabili.html](full_copies/LEAD-06-per-run-artificial-intelligence-and-the-future-of-human-rights-legal-vs-lt-b-gt-artificial-intelligence-and-algorithmic-accountabili.html) *(per-passage view)* | full copy | International Research Institute Pakistan | 97% / 94% | 8,758 / 9,037 |
| LEAD-08 | [LEAD-08-the-impact-of-artificial-intelligence-on-business-amp-social-vs-unveiling-the-potential-of-ai-impacts-on-industries-and-ethi.html](full_copies/LEAD-08-the-impact-of-artificial-intelligence-on-business-amp-social-vs-unveiling-the-potential-of-ai-impacts-on-industries-and-ethi.html) | full copy | Green Publication | 83% / 77% | 4,389 / 4,756 |
| LEAD-17 | [LEAD-17-surgical-management-of-intercondylar-fracture-of-distal-hume-vs-surgical-management-of-distal-humeral-fractures-in-adults.html](partial_overlap/LEAD-17-surgical-management-of-intercondylar-fracture-of-distal-hume-vs-surgical-management-of-distal-humeral-fractures-in-adults.html) | partial | AkiNik Publications | 59% / 58% | 1,915 / 2,060 |
| LEAD-15d | [LEAD-15d-the-role-of-ai-in-cybersecurity-addressing-threats-in-the-di-vs-ai-for-sustainable-development-addressing-environmental-and-.html](partial_overlap/LEAD-15d-the-role-of-ai-in-cybersecurity-addressing-threats-in-the-di-vs-ai-for-sustainable-development-addressing-environmental-and-.html) | partial | Open Knowledge | 9% / 10% | 3,072 / 2,755 |
| LEAD-15g | [LEAD-15g-adaptive-and-context-aware-authentication-framework-using-ed-vs-a-novel-authentication-systems-in-vehicular-communication-ch.html](partial_overlap/LEAD-15g-adaptive-and-context-aware-authentication-framework-using-ed-vs-a-novel-authentication-systems-in-vehicular-communication-ch.html) | partial | Smart Technologies Academic Press | 21% / 21% | 2,970 / 2,992 |

### Different publishers (27 pairs)

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
| LEAD-03 | [LEAD-03-risk-assessment-tools-in-criminal-justice-and-forensic-psych-vs-artificial-intelligence-in-the-court-justice-system.html](partial_overlap/LEAD-03-risk-assessment-tools-in-criminal-justice-and-forensic-psych-vs-artificial-intelligence-in-the-court-justice-system.html) | partial | Cambridge University Press / University of Niš | 16% / 9% | 2,887 / 4,898 |
| LEAD-04 | [LEAD-04-operationalizing-generative-ai-in-software-product-managemen-vs-empowering-business-transformation-the-positive-impact-and-e.html](partial_overlap/LEAD-04-operationalizing-generative-ai-in-software-product-managemen-vs-empowering-business-transformation-the-positive-impact-and-e.html) | partial | arXiv / Open Engineering Inc (engrXiv) | 41% / 66% | 8,726 / 5,281 |
| LEAD-15 | [LEAD-15-from-google-gemini-to-openai-q-q-star-a-survey-of-reshaping--vs-from-bard-to-gemini-an-investigative-exploration-journey-thr.html](partial_overlap/LEAD-15-from-google-gemini-to-openai-q-q-star-a-survey-of-reshaping--vs-from-bard-to-gemini-an-investigative-exploration-journey-thr.html) | partial | arXiv / Academic Publishing Pte. Ltd. | 2% / 5% | 17,167 / 7,328 |
| LEAD-18 | [LEAD-18-is-a-drain-tip-culture-required-after-spinal-surgery-vs-drain-tip-culture-would-it-help-us-predict-and-prevent-surgi.html](partial_overlap/LEAD-18-is-a-drain-tip-culture-required-after-spinal-surgery-vs-drain-tip-culture-would-it-help-us-predict-and-prevent-surgi.html) | partial | Wolters Kluwer Health / International Journal of Pharmaceutical and Clinical Research | 16% / 17% | 2,045 / 2,004 |
| LEAD-07 | [LEAD-07-exploring-ethical-considerations-in-ai-driven-autonomous-veh-vs-finding-differences-in-perspectives-between-designers-and-en.html](partial_overlap/LEAD-07-exploring-ethical-considerations-in-ai-driven-autonomous-veh-vs-finding-differences-in-perspectives-between-designers-and-en.html) | partial | Open Knowledge / Luleå University of Technology | 8% / 3% | 4,714 / 14,146 |
| LEAD-09 | [LEAD-09-guidelines-for-securing-radio-frequency-identification-rfid--vs-multi-agent-rfid-process-in-project-chain-management.html](partial_overlap/LEAD-09-guidelines-for-securing-radio-frequency-identification-rfid--vs-multi-agent-rfid-process-in-project-chain-management.html) | partial | National Institute of Standards and Technology / Science Arena Publications | 2% / 32% | 54,640 / 3,130 |
| LEAD-10 | [LEAD-10-review-of-mathematical-frameworks-for-fairness-in-machine-le-vs-fairness-metrics-a-comparative-analysis.html](partial_overlap/LEAD-10-review-of-mathematical-frameworks-for-fairness-in-machine-le-vs-fairness-metrics-a-comparative-analysis.html) | partial | arXiv / IEEE | 4% / 8% | 12,891 / 6,115 |
| LEAD-15a | [LEAD-15a-a-study-of-cyber-security-challenges-and-its-emerging-trends-vs-a-review-on-cybersecurity-issues-and-emerging-trends-in-mode.html](partial_overlap/LEAD-15a-a-study-of-cyber-security-challenges-and-its-emerging-trends-vs-a-review-on-cybersecurity-issues-and-emerging-trends-in-mode.html) | partial | arXiv / Zenodo | 14% / 17% | 2,724 / 2,166 |
| LEAD-15b | [LEAD-15b-enabling-identity-based-integrity-auditing-and-data-sharing--vs-improving-security-in-cloud-storage-auditing-by-identity-hid.html](partial_overlap/LEAD-15b-enabling-identity-based-integrity-auditing-and-data-sharing--vs-improving-security-in-cloud-storage-auditing-by-identity-hid.html) | partial | IEEE / Zestera Publications | 8% / 28% | 11,514 / 2,936 |
| LEAD-15e | [LEAD-15e-explainable-ai-for-cloud-based-machine-learning-interpretabl-vs-transparency-and-interpretability-in-cloudbased-machine-lear.html](partial_overlap/LEAD-15e-explainable-ai-for-cloud-based-machine-learning-interpretabl-vs-transparency-and-interpretability-in-cloudbased-machine-lear.html) | partial | Science Research Society / Ess & Ess Research Publications | 7% / 9% | 3,953 / 3,192 |
| LEAD-15f | [LEAD-15f-the-role-of-artificial-intelligence-in-advancing-public-serv-vs-the-impact-of-artificial-governance-on-indian-public-adminis.html](partial_overlap/LEAD-15f-the-role-of-artificial-intelligence-in-advancing-public-serv-vs-the-impact-of-artificial-governance-on-indian-public-adminis.html) | partial | Goacademica Research and Publishing / Zenodo | 7% / 21% | 6,457 / 2,047 |
| LEAD-20 | [LEAD-20-attendance-management-system-using-facial-recognition-vs-attendance-management-system-based-on-facial-recognition.html](partial_overlap/LEAD-20-attendance-management-system-using-facial-recognition-vs-attendance-management-system-based-on-facial-recognition.html) | partial | Zain Publications / International Journal for Research in Applied Science and Engineering Technology | 4% / 3% | 2,827 / 2,739 |
| LEAD-19 | [LEAD-19-an-enhanced-multi-layered-cryptosystem-based-secure-and-auth-vs-secure-auditing-and-deduplicating-data-in-cloud-published-ab.html](partial_overlap/LEAD-19-an-enhanced-multi-layered-cryptosystem-based-secure-and-auth-vs-secure-auditing-and-deduplicating-data-in-cloud-published-ab.html) | partial | IEEE / AI Publications | 92% / 12% | 173 / 1,613 |

`LEAD-07`: the second document is a master's thesis from Luleå University of Technology with no DOI; its
publisher is taken from the thesis itself.
`LEAD-09`: Crossref lists the second document's DOI (10.51847/4hrmacreqz) as deleted, so its title, year
and authors on the page are taken from the PDF: "Multi Agent Rfid Process In Project Chain Management",
*Specialty Journal of Psychology and Management* (Science Arena Publications), 2015.

`LEAD-20`: the second document rewords the first sentence by sentence rather than repeating it, so
the standard 10-word measurement above finds little (4% / 3%). Its page is rendered with 5-word matches
and a wider substitution allowance (the footer names the settings) so the reworded passages are visible.

`LEAD-19`: the first document is the published abstract of an IEEE Transactions on Computers paper
(Li, Li, Xie and Cai, *Secure Auditing and Deduplicating Data in Cloud*) whose full text is not openly
available, so the "A" figure is the share of that 173-word abstract, not of the whole paper. The second
document repeats nearly all of it.
