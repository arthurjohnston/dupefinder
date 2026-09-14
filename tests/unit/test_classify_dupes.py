#!/usr/bin/env python3
"""Unit tests for classify_dupes.py's pattern library -- in particular the
patterns added 2026-08-24 (todo.md post-mortem 7) after hand-reading a
100-row sample of unclassified candidates found several boilerplate
categories the original "AI pre-filter pass" pattern list never saw."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import classify_dupes as cd  # noqa: E402


class TestClassifyTextNewPatterns(unittest.TestCase):
    def test_journal_masthead_homepage_link(self):
        text = ("Scholars Journal of Engineering and Technology Abbreviated Key Title: Sch J Eng Tech "
                "ISSN 2347-9523 (Print) | ISSN 2321-435X (Online) Journal homepage: https://saspublishers.com")
        self.assertIsNotNone(cd.classify_text(text))

    def test_springer_frontiers_publishers_note(self):
        text = ("De Micco et al. 10.3389/fmed.2024.1428504 Frontiers in Medicine 09 frontiersin.org "
                "Publisher's note All claims expressed in this article are solely those of the authors")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ntis_report_distribution_boilerplate(self):
        text = ("U.S. DEPARTMENT OF COMMERCE National Technical Information Service 5285 Port Royal Road "
                "Springfield, Virginia 22161 POSTAGE AND FEES PAID")
        self.assertIsNotNone(cd.classify_text(text))

    def test_copernicus_egu_website_navigation_chrome(self):
        text = ("Y. Y. Zhang et al. Title Page Abstract Introduction Conclusions References Tables Figures "
                "◀ ▶ ◀ ▶ Back Close Full Screen / Esc Printer-friendly Version")
        self.assertIsNotNone(cd.classify_text(text))

    def test_please_cite_this_paper_as(self):
        text = "Please cite this paper as: Valizadeh Toosi SM, Elahi Vahed AR, Maleki I, Bari Z."
        self.assertIsNotNone(cd.classify_text(text))

    def test_electronic_supplementary_material(self):
        text = ("Electronic supplementary material The online version of this article "
                "(doi:10.1007/s12639-015-0703-z) contains supplementary material")
        self.assertIsNotNone(cd.classify_text(text))

    def test_numbered_bibliography_entry_latin(self):
        text = "22. Auerbach, T., Burnard, G., Soodak, H. Heterogeneous method and its application."
        self.assertIsNotNone(cd.classify_text(text))

    def test_numbered_bibliography_entry_cyrillic(self):
        text = "5. Комаров, С. А. Общая теория."
        self.assertIsNotNone(cd.classify_text(text))

    def test_ordinary_section_numbering_not_misfired(self):
        # A numbered section heading followed by a normal sentence (not "Lastname, Initial.")
        # shouldn't trip the numbered-bibliography pattern.
        text = "3. Results We found that the proposed method outperforms the baseline by a wide margin."
        # It may or may not be classified by some OTHER pattern -- just confirm the bibliography
        # pattern specifically isn't why, by checking the reason text when something does match.
        reason = cd.classify_text(text)
        if reason is not None:
            self.assertNotIn("numbered bibliography", reason)

    # Patterns added after reading rank 101-250 of the same ranked sample (todo.md post-mortem 7, second batch).

    def test_springer_nature_jurisdictional_claims_note(self):
        text = ("The original article has been corrected. Publisher's Note Springer Nature remains "
                "neutral with regard to jurisdictional claims in published maps and institutional affiliations.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_glasgow_enlighten_repository_deposit_stamp(self):
        text = ("http://eprints.gla.ac.uk/285185/ Deposited on: 16 November 2022 Enlighten – Research "
                "publications by members of the University of Glasgow http://eprints.gla.ac.uk")
        self.assertIsNotNone(cd.classify_text(text))

    def test_doe_national_lab_funding_acknowledgment(self):
        text = ("This work was performed under the auspices of the U.S. Department of Energy by "
                "Lawrence Livermore National Laboratory under contract No. W-7405-Eng-48.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_doe_osti_report_distribution_boilerplate(self):
        text = ("This report has been reproduced directly from the best available copy. Available to "
                "DOE and DOE contractors from the Office of Scientific and Technical Information")
        self.assertIsNotNone(cd.classify_text(text))

    def test_gpo_superintendent_of_documents_boilerplate(self):
        text = "U.S. GOVERNMENT PRINTING OFFICE WASHINGTON: 1996 For sale by the Superintendent of Documents"
        self.assertIsNotNone(cd.classify_text(text))

    def test_crui_care_funding_agreement_boilerplate(self):
        text = ("Funding Open access funding provided by Alma Mater Studiorum - Università di Bologna "
                "within the CRUI-CARE Agreement.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_crue_csic_funding_agreement_boilerplate(self):
        text = "Funding Open Access funding provided thanks to the CRUE-CSIC agreement with Springer Nature."
        self.assertIsNotNone(cd.classify_text(text))

    # Patterns added after re-running find_review_candidates.py a third time (todo.md post-mortem 7, third batch).

    def test_declaration_of_interest_boilerplate(self):
        text = ("Declaration of Interest The authors declare that they have no known competing financial "
                "interests or personal relationships that could have appeared to influence the work reported "
                "in this paper.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ethics_approval_declarations_block_boilerplate(self):
        text = ("Declarations Ethics approval and consent to participate Not applicable. Consent for "
                "publication Not applicable. Competing interests No Competing interests.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_dutch_article_25fa_copyright_boilerplate(self):
        text = ("DOI (link to publisher): 10.1177/00472816251384898 Document Version Publisher's PDF, also "
                "known as Version of record Document License/Available under: Article 25fa Dutch Copyright Act")
        self.assertIsNotNone(cd.classify_text(text))

    def test_frontiers_supplementary_material_notice_boilerplate(self):
        text = ("Supplementary material The Supplementary Material for this article can be found online at: "
                "https://www.frontiersin.org/articles/10.3389/fdgth.2026.1756256/full#supplementary-material")
        self.assertIsNotNone(cd.classify_text(text))

    def test_medrxiv_preprint_license_boilerplate(self):
        text = ("Word count: 3,131 . CC-BY-NC-ND 4.0 International license It is made available under a is "
                "the author/funder, who has granted medRxiv a license to display the preprint in perpetuity.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_hmso_monograph_copyright_boilerplate(self):
        text = "ISSN 1366-5278 © Queen's Printer and Controller of HMSO 2004 This monograph may be freely reproduced"
        self.assertIsNotNone(cd.classify_text(text))

    def test_journal_homepage_without_colon_now_matches(self):
        text = ("RESEARCH ARTICLE 2 Access to Justice in Eastern Europe ISSN 2663-0575 (Print) "
                "ISSN 2663-0583 (Online) Journal homepage http://ajee-journal.com 1 INTRODUCTION")
        self.assertIsNotNone(cd.classify_text(text))

    def test_french_journal_platform_footer_boilerplate(self):
        text = ("Le texte et les autres éléments (illustrations, fichiers annexes importés), sont "
                "« Tous droits réservés », sauf mention contraire. 11 DOSSIER Olivier Aïm")
        self.assertIsNotNone(cd.classify_text(text))

    def test_bioinformation_journal_citation_template(self):
        text = ("Edited by P. Kangueane Citation: Praveen et al., Bioinformation 1(1): 14-15 (2005) "
                "License statement: This is an open-access article, which permits unrestricted use")
        self.assertIsNotNone(cd.classify_text(text))

    # Pattern added after switching to --sort gap (todo.md post-mortem 7, fourth batch).

    def test_thesis_by_publication_declaration_boilerplate(self):
        text = ("Publications can be used in their thesis in lieu of a Chapter if: • The student "
                "contributed greater than 50% of the content in the publication and is the “primary "
                "author”")
        self.assertIsNotNone(cd.classify_text(text))

    def test_thesis_by_publication_declaration_boilerplate_candidate_variant(self):
        text = ("Publications can be used in the candidate's thesis in lieu of a Chapter provided: "
                "The candidate contributed greater than 50% of the content in the publication")
        self.assertIsNotNone(cd.classify_text(text))

    # Patterns added after checking rows with lcs_ratio=1 or ngram_jaccard=1 in the unclassified pool.

    def test_tu_eindhoven_general_rights_boilerplate(self):
        text = ("General rights Copyright and moral rights for the publications made accessible in the "
                "public portal are retained by the authors and/or other copyright owners")
        self.assertIsNotNone(cd.classify_text(text))

    def test_tu_eindhoven_document_version_notice_boilerplate(self):
        text = "Please check the document version of this publication: • A submitted manuscript is the version"
        self.assertIsNotNone(cd.classify_text(text))

    def test_tu_eindhoven_take_down_policy_boilerplate(self):
        text = "Take down policy If you believe that this document breaches copyright please contact us providing details"
        self.assertIsNotNone(cd.classify_text(text))

    def test_eur_rotterdam_take_down_policy_boilerplate(self):
        text = ("Take-down policy If you believe that this material infringes your copyright and/or any "
                "other intellectual property rights, you may request its removal by contacting us at "
                "the following email address: openaccess.library@eur.nl")
        self.assertIsNotNone(cd.classify_text(text))

    def test_accepted_version_notice_boilerplate(self):
        text = "This is the accepted version of the paper. This version of the publication may differ from the final published version."
        self.assertIsNotNone(cd.classify_text(text))

    def test_cc_personal_use_only_paraphrase_boilerplate(self):
        text = ("Creative Commons Licence, you must assume that re-use is limited to personal use and "
                "that permission from the copyright owner must be obtained for all other uses. If the docu-")
        self.assertIsNotNone(cd.classify_text(text))

    def test_scelc_funding_boilerplate(self):
        text = ("Funding Open access funding provided by SCELC, Statewide California Electronic Library "
                "Consortium Data availability No datasets were generated")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ieee_adcom_members_boilerplate(self):
        text = "Administrative Committee (AdCom) Members: Carole Graas, Evelyn Hirt, Qiang Miao, J. Bret Michael"
        self.assertIsNotNone(cd.classify_text(text))

    def test_palgrave_macmillan_trademark_boilerplate(self):
        text = ("Palgrave® and Macmillan® are registered trademarks in the United States, the United "
                "Kingdom, Europe and other countries.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_cambridge_university_press_copyright_boilerplate(self):
        text = ("This work is in copyright. It is subject to statutory exceptions and to the provisions "
                "of relevant licensing agreements; with the exception of the Creative Commons version")
        self.assertIsNotNone(cd.classify_text(text))

    def test_redfame_publishing_ethics_boilerplate(self):
        text = ("Informed consent Obtained. Ethics approval The Publication Ethics Committee of the "
                "Redfame Publishing. The journal's policies adhere to the Core Practices")
        self.assertIsNotNone(cd.classify_text(text))

    def test_tspace_citation_notice_boilerplate(self):
        text = "How to cite TSpace items Always cite the published version, so the author(s) will receive recognition"
        self.assertIsNotNone(cd.classify_text(text))

    def test_ncchta_credit_card_ordering_boilerplate(self):
        text = ("Paying by credit card The following cards are accepted by phone, fax, post or via the "
                "website ordering pages: Delta, Eurocard, Mastercard, Solo, Switch and Visa.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ncchta_cheque_ordering_boilerplate(self):
        text = ("Paying by cheque If you pay by cheque, the cheque must be in pounds sterling, made "
                "payable to University of Southampton and drawn on a bank with a UK address.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ncchta_gray_publishing_imprint_boilerplate(self):
        text = "Published by Gray Publishing, Tunbridge Wells, Kent, on behalf of NCCHTA."
        self.assertIsNotNone(cd.classify_text(text))

    def test_nist_locations_address_boilerplate(self):
        text = ("located at Gaithersburg, MD 20899, and at Boulder, CO 80303. Major technical operating "
                "units and their principal activities are listed below.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_sandia_national_laboratories_doe_contract_boilerplate(self):
        text = ("Sandia is a multiprogram laboratory operated by Sandia Corporation, a Lockheed Martin "
                "Company, for the United States Department of Energy's National Nuclear Security "
                "Administration under Contract DE-AC04-94AL85000.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ntis_address_block_boilerplate(self):
        text = ("5285 Port Royal Rd. Springfield, VA 22161 Telephone: (800) 553-6847 Facsimile: "
                "(703) 605-6900 E-Mail: orders@ntis.fedworld.gov")
        self.assertIsNotNone(cd.classify_text(text))

    def test_springer_jurisdictional_claims_boilerplate_with_dehyphenation_space(self):
        # pdftotext sometimes emits "juris dictional" (with a space) instead of "jurisdictional" --
        # the plain-spelling version of this pattern missed real matches because of it.
        text = ("Publisher's Note Springer Nature remains neutral with regard to juris dictional claims "
                "in published maps and institutional affiliations.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_springer_cc_by_permission_paragraph_boilerplate(self):
        text = ("If material is not included in the article's Creative Commons licence and your intended "
                "use is not permitted by statutory regulation or exceeds the permitted use, you will need "
                "to obtain permission directly from the copyright holder.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ijcsmc_masthead_boilerplate(self):
        text = ("Available Online at www.ijcsmc.com International Journal of Computer Science and Mobile "
                "Computing A Monthly Journal of Computer Science and Information Technology")
        self.assertIsNotNone(cd.classify_text(text))

    def test_turkish_funding_disclosure_boilerplate(self):
        text = ("bir ticari firmadan, çalışmanın değerlendirme sürecinde, çalışma ile ilgili verilecek "
                "kararı olumsuz etkileyebilecek maddi ve/veya manevi herhangi bir destek alınmamıştır.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_turkish_author_contributions_heading_boilerplate(self):
        text = ("herhangi bir firmada çalışma durumu, hissedarlık ve benzer durumları yoktur. "
                "Yazar Katkıları")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ieee_jobs_board_filler_boilerplate(self):
        text = ("Whether you enjoy your current position or you are ready for change, the IEEE Computer "
                "Society Jobs Board is a valuable resource tool.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_german_repository_noncommercial_licence_boilerplate(self):
        text = ("Sie dürfen die Dokumente nicht für öffentliche oder kommerzielle Zwecke vervielfältigen, "
                "öffentlich ausstellen, öffentlich zugänglich machen, vertreiben oder anderweitig nutzen.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_jurisdictional_claims_matches_without_remains_neutral_lead_in(self):
        # MDPI/other publishers sometimes split the paragraph so only the tail clause survives.
        text = "with regard to jurisdictional claims in published maps and institutional affiliations."
        self.assertIsNotNone(cd.classify_text(text))

    def test_jobs_board_matches_without_leading_ieee(self):
        text = ("Come to the Computer Society Jobs Board to meet the best employers in the "
                "industry—Apple, Google, Intel, NSA, Cisco, US Army Research, Oracle, Juniper...")
        self.assertIsNotNone(cd.classify_text(text))

    def test_oecd_attribution_translations_clause_boilerplate(self):
        text = ("Attribution – you must cite the work. Translations – you must cite the "
                "original work, identify changes to the original and add the following text: In the "
                "event of any discrepancy between the original work and the translation, only the text "
                "of original work should be considered valid.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_fed_atlanta_working_paper_footer_boilerplate(self):
        text = ("Federal Reserve Bank of Atlanta working papers, including revised versions, are "
                "available on the Atlanta Fed's Web site at www.frbatlanta.org.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_adb_cc_license_disclaimer_boilerplate(self):
        text = ("This CC license does not apply to non-ADB copyright materials in this publication. "
                "If the material is attributed to another source, please contact the copyright owner "
                "or publisher of that source for permission to reproduce it.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_intechopen_masthead_boilerplate(self):
        text = ("www.intechopen.com InTech China Unit 405, Office Block, Hotel Equatorial Shanghai "
                "No.65, Yan An Road (West), Shanghai, 200040, China")
        self.assertIsNotNone(cd.classify_text(text))

    def test_german_drittmaterial_clause_boilerplate_no_spaces(self):
        # pdftotext frequently glues this justified-column German text into one run with no spaces.
        text = ("DieindiesemArtikelenthaltenenBilderundsonstiges "
                "Drittmaterialunterliegenebenfallsdergenannten CreativeCommonsLizenz")
        self.assertIsNotNone(cd.classify_text(text))

    def test_lshtm_repository_licence_notice_boilerplate(self):
        text = ("Available under license. To note, 3rd party material is not necessarily covered under "
                "this license: http://creativecommons.org/licenses/by-nc-nd/4.0/ "
                "https://researchonline.lshtm.ac.uk")
        self.assertIsNotNone(cd.classify_text(text))

    def test_unsw_candidates_declaration_boilerplate(self):
        text = ("CANDIDATE'S DECLARATION I declare that: • I have complied with the UNSW Thesis "
                "Examination Procedure • where I have used a publication in lieu of a Chapter, the "
                "listed publication(s) below meet(s) the requirements to be included in the thesis.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_nist_boulder_address_only_variant_boilerplate(self):
        text = ("Headquarters and Laboratories at Gaithersburg, MD, unless otherwise noted; mailing "
                "address Washington, DC 20234. Some divisions within the center are located at "
                "Boulder, CO 80303.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_nist_calibration_services_mission_statement_boilerplate(self):
        text = ("agencies; develops, produces, and distributes Standard Reference Materials; and "
                "provides calibration services. The Laboratory consists of the following centers:")
        self.assertIsNotNone(cd.classify_text(text))

    def test_epa_pesticide_fact_sheet_study_template_boilerplate(self):
        text = ("The studies include data on the fate in animals (rats), fate in plants (potatoes, "
                "lettuce, etc.), residues in crops, sub-acute toxicity (rats, mice and dogs)")
        self.assertIsNotNone(cd.classify_text(text))

    def test_jmir_xsl_fo_renderx_artifact_boilerplate(self):
        text = "XSL•FO RenderX Data Availability Deidentified data are available for research purposes"
        self.assertIsNotNone(cd.classify_text(text))

    def test_sdss_iii_funding_acknowledgment_boilerplate(self):
        text = ("Funding for SDSS-III has been provided by the Alfred P. Sloan Foundation, the "
                "Participating Institutions, the National Science Foundation, and the U.S. Department "
                "of Energy Office of Science.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ijet_sciencepubco_masthead_boilerplate(self):
        text = ("International Journal of Engineering & Technology, 7 (4.15) (2018) 87-92 International "
                "Journal of Engineering & Technology Website: www.sciencepubco.com/index.php/IJET")
        self.assertIsNotNone(cd.classify_text(text))

    def test_jhass_hillpublisher_masthead_boilerplate(self):
        text = ("Journal of Humanities, Arts and Social Science, 2023, 7(1), 128-154 "
                "https://www.hillpublisher.com/journals/jhass/ ISSN Online: 2576-0548 ISSN Print: 2576-0556")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ieee_retraction_notice_boilerplate(self):
        text = ("After careful and considered review of the content and authorship of this paper by a "
                "duly constituted expert committee, this paper has been found to be in violation of "
                "IEEE's Publication Principles.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_bibliography_entry_without_comma_between_surname_and_initials(self):
        # "Lastname Initial." with no comma -- the earlier pattern required a comma and missed this.
        text = ("5. Odeniran OM. Exploring the Potential of Bambara Groundnut Flour as an Alternative "
                "for Diabetic and Obese Patients in the USA: A Comprehensive Review.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_numbered_section_heading_still_not_misfired_by_broadened_bibliography_pattern(self):
        text = "3. Results We found that the proposed method outperforms the baseline by a wide margin."
        reason = cd.classify_text(text)
        if reason is not None:
            self.assertNotIn("numbered bibliography", reason)

    def test_brics_technical_report_footer_boilerplate(self):
        text = ("BRICS publications are in general accessible through the World Wide Web and anonymous "
                "FTP through these URLs: http://www.brics.dk ftp://ftp.brics.dk")
        self.assertIsNotNone(cd.classify_text(text))

    def test_bond_university_law_repository_boilerplate(self):
        text = ("This Article is brought to you by the Faculty of Law at ePublications@bond. It has "
                "been accepted for inclusion in Bond Law Review by an authorized administrator.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_jhea_repository_deposit_notice_boilerplate(self):
        text = ("This work is brought to you for free and open access by the Institute of Clinical "
                "Bioethics (ICB) at Saint Joseph's University, Philadelphia, PA, U.S.A.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ottawa_civil_law_repository_boilerplate(self):
        text = ("Droits d'auteur © Faculté de droit, Section de droit civil, Université d'Ottawa, 2014 "
                "Ce document est protégé par la loi sur le droit d'auteur.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_lanl_doe_contract_boilerplate(self):
        text = ("Los Alamos National Laboratory, an affirmative action/equal opportunity employer, is "
                "operated by the University of California for the United States Department of Energy "
                "under contract W-7405-ENG-36.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_doe_report_liability_disclaimer_boilerplate(self):
        text = ("This report was prepared as an account of work sponsored by an agency of the United "
                "States Government. Neither the Regents of the University of California, the United "
                "States Government nor any agency thereof, nor any of their employees make any warranty")
        self.assertIsNotNone(cd.classify_text(text))

    def test_nrel_address_imprint_boilerplate(self):
        text = ("National Renewable Energy Laboratory 1617 Cole Boulevard Golden, Colorado 80401-3393 "
                "NREL is a U.S. Department of Energy Laboratory")
        self.assertIsNotNone(cd.classify_text(text))

    def test_aaai_symposium_committee_boilerplate(self):
        text = ("All proposals will be reviewed by the AAAI Symposium Committee (Chair: Matthew E. "
                "Taylor, Lafayette College; Cochair: Gita Sukthankar, University of Central Florida)")
        self.assertIsNotNone(cd.classify_text(text))

    def test_jcmcc_masthead_boilerplate(self):
        text = ("J. COMBIN. MATH. COMBIN. COMPUT. 127b (2025) 8359-8371 Journal of Combinatorial "
                "Mathematics and Combinatorial Computing www.combinatorialpress.com/jcmcc")
        self.assertIsNotNone(cd.classify_text(text))

    def test_generic_publisher_ccbyncnd_paraphrase_boilerplate(self):
        text = ("to download articles and share them with others as long as they credit the authors "
                "and the publisher, but without permission to change them in any way or use them "
                "commercially.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_white_rose_repository_boilerplate(self):
        text = ("eprints@whiterose.ac.uk https://eprints.whiterose.ac.uk Universities of Leeds, "
                "Sheffield and York Deposited via The University of Sheffield.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ieee_crosscheck_cfp_filler_boilerplate(self):
        text = ("All submissions must adhere to IEEE Publishing Policies, and will be vetted through "
                "the IEEE CrossCheck portal.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_gjetr_masthead_boilerplate(self):
        text = ("Available on: http://gjetr.ep-journals.org/ E-ISSN: 3051-3782, P-ISSN: 3051-3774 "
                "Global Journal of Engineering and Technology Review")
        self.assertIsNotNone(cd.classify_text(text))

    def test_argonne_doe_contract_boilerplate(self):
        text = ("The submitted manuscript has been created by the UChicago Argonne, LLC, Operator of "
                "Argonne National Laboratory (Argonne). Argonne, a U.S. Department of Energy Office of "
                "Science laboratory, is operated under Contract No. DE-AC02-06CH11357.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_generic_ieee_copyright_notice_boilerplate(self):
        text = ("Personal use is also permitted, but republication/redistribution requires IEEE "
                "permission. See http://www.ieee.org/publications_standards/publications/rights/index.html")
        self.assertIsNotNone(cd.classify_text(text))

    def test_frontiers_research_topic_contact_boilerplate(self):
        text = ("Find out more on how to host your own Frontiers Research Topic or contribute to one "
                "as an author by contacting the Frontiers editorial office: frontiersin.org/about/contact")
        self.assertIsNotNone(cd.classify_text(text))

    def test_unsw_restricted_access_declaration_boilerplate(self):
        text = ("The University recognises that there may be exceptional circumstances requiring "
                "restrictions on copying or conditions on use. Requests for restriction for a period of")
        self.assertIsNotNone(cd.classify_text(text))

    def test_itsi_transactions_masthead_boilerplate(self):
        text = ("ITSI Transactions on Electrical and Electronics Engineering © 2023 The Authors. "
                "Published by MRI INDIA.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_bioexcel_publishing_imprint_boilerplate(self):
        text = ("BioExcel Publishing Limited is registered in England Number 10038393. VAT GB 252 "
                "7720 07. For all manuscript and submissions enquiries, contact the Editorial office")
        self.assertIsNotNone(cd.classify_text(text))

    def test_watts_humphrey_award_filler_boilerplate(self):
        text = ("Since 1994, the SEI and the Institute of Electrical and Electronics Engineers (IEEE) "
                "Computer Society have cosponsored the Watts S. Humphrey Software Quality Award")
        self.assertIsNotNone(cd.classify_text(text))

    def test_generic_thesis_copyright_retention_boilerplate(self):
        text = ("The author retains ownership of the copyright in this thesis. Neither the thesis nor "
                "substantial extracts from it may be printed or otherwise reproduced without the "
                "author's permission.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_german_thesis_sworn_declaration_boilerplate(self):
        text = ("Ich erkläre hiermit ehrenwörtlich, dass ich die vorliegende Arbeit selbstständig "
                "angefertigt habe.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_sandia_alternate_wording_boilerplate(self):
        text = ("Sandia National Laboratories is a multi-program laboratory managed and operated by "
                "Sandia Corporation, a wholly owned subsidiary of Lockheed Martin Corporation")
        self.assertIsNotNone(cd.classify_text(text))

    def test_generic_trade_name_disclaimer_boilerplate(self):
        text = ("Reference in the manuscript to any specific commercial product, process or service by "
                "trade name, trademark, manufacturer or otherwise does not necessarily constitute or "
                "imply its endorsement, recommendation or favouring by the authors")
        self.assertIsNotNone(cd.classify_text(text))

    def test_russian_legal_conference_journal_list_boilerplate(self):
        text = ("The materials of the conference will be published in the journals Theory of State and "
                "Law, Legal Thought, Contemporary Russian Law, Bulletin of the Institute of Law")
        self.assertIsNotNone(cd.classify_text(text))

    def test_declaration_of_generative_ai_boilerplate(self):
        text = ("Declaration of generative AI in scientific writing We employed generative AI in the "
                "writing process, especially aiming to improve the text quality.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_icrc_views_disclaimer_boilerplate(self):
        text = ("The advice, opinions and statements contained in this article are those of the "
                "author/s and do not necessarily reflect the views of the ICRC.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_adb_contact_permissions_boilerplate(self):
        text = ("Please contact pubsmarketing@adb.org if you have questions or comments with respect "
                "to content, or if you wish to obtain copyright permission for your intended use")
        self.assertIsNotNone(cd.classify_text(text))

    def test_philadelphia_fed_working_paper_disclaimer_boilerplate(self):
        text = ("ISSN: 1962-5361 Disclaimer: This Philadelphia Fed working paper represents preliminary "
                "research that is being circulated for discussion purposes.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_frontiers_journal_series_frontmatter_boilerplate(self):
        text = ("Frontiers journal series The Frontiers journal series is a multi-tier and "
                "interdisciplinary set of open-access, online journals, promising a paradigm shift")
        self.assertIsNotNone(cd.classify_text(text))

    def test_chatham_house_imprint_boilerplate(self):
        text = ("Chatham House, the Royal Institute of International Affairs, is a world-leading "
                "policy institute based in London.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_apsdpr_masthead_boilerplate(self):
        text = ("http://www.apsdpr.org Open Access Africa's Public Service Delivery and Performance "
                "Review ISSN: (Online) 2310-2152")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ieee_locs_open_access_boilerplate(self):
        text = ("OPEN ACCESS LOCS offers open access options for authors. Learn more about IEEE open "
                "access publishing: https://open.ieee.org")
        self.assertIsNotNone(cd.classify_text(text))

    def test_lse_research_online_boilerplate(self):
        text = ("https://researchonline.lse.ac.uk/id/eprint/126068/ Version: Accepted Version This "
                "document is the author's accepted version of the journal article.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_sinergi_journal_masthead_boilerplate(self):
        text = ("Sinergi International Journal of Islamic Studies E-ISSN: 2988-7445 Volume. 3, Issue "
                "2, May 2025 Page No: 129-138")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ijsat_masthead_boilerplate(self):
        text = ("International Journal on Science and Technology (IJSAT) E-ISSN: 2229-7677 Website: "
                "www.ijsat.org Email: editor@ijsat.org")
        self.assertIsNotNone(cd.classify_text(text))

    def test_eduzone_masthead_boilerplate(self):
        text = ("EDUZONE: International Peer Reviewed/Refereed Multidisciplinary Journal (EIPRMJ), "
                "ISSN: 2319-5045 Volume 5, Issue 2, July-December, 2016")
        self.assertIsNotNone(cd.classify_text(text))

    def test_lipics_dagstuhl_imprint_boilerplate(self):
        text = ("Leibniz International Proceedings in Informatics Schloss Dagstuhl – Leibniz-Zentrum "
                "für Informatik, Dagstuhl Publishing, Germany")
        self.assertIsNotNone(cd.classify_text(text))

    def test_pasupuleti_throwaway_citation_boilerplate(self):
        text = ("pp. 18–31. Pasupuleti, MK 2023c, 'Topological and Software-Driven Advances in "
                "Robotics: Leveraging MATLAB-Simulink and Semiconductor Innovation for Adaptive")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ieee_computer_society_member_benefits_filler_boilerplate(self):
        text = ("IEEE Computer Society Has You Covered! WORLD-CLASS CONFERENCES — Stay ahead of the "
                "curve by attending one of our 200+ globally recognized conferences.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_ieee_cite_original_published_paper_notice_boilerplate(self):
        text = ("N.B. When citing this work, cite the original published paper. © 2021 IEEE. Personal "
                "use of this material is permitted.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_stratford_journal_masthead_boilerplate(self):
        text = ("Stratford Peer Reviewed Journals and Book Publishing Journal of Public Policy and "
                "Governance Volume 7||Issue 3||Page 1-12||August||2023")
        self.assertIsNotNone(cd.classify_text(text))

    def test_hindawi_mass_retraction_notice_boilerplate(self):
        text = ("The publisher has retracted this article in agreement with the Editor-in-Chief. The "
                "article was submitted to be part of a guest-edited issue. An investigation by the "
                "publisher found a number of articles, including this one, with a number of concerns")
        self.assertIsNotNone(cd.classify_text(text))

    def test_aaai_symposia_registration_boilerplate(self):
        text = ("Complete registration and hotel information will be available in late July at "
                "www.aaai.org/Symposia/Fall/fss19.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_hindawi_ethics_helsinki_declaration_boilerplate(self):
        text = ("Ethical Approval All procedures performed in studies involving human participants "
                "were in accordance with the ethical standards of the institutional and/or national "
                "research committee and with the 1964 Helsinki declaration and its later amendments")
        self.assertIsNotNone(cd.classify_text(text))

    def test_hindawi_data_availability_boilerplate(self):
        text = ("Data Availability The data used to support the findings of this study are from "
                "previously reported studies and public database, which have been cited.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_unsw_thesis_contribution_acknowledged_boilerplate(self):
        # Real corpus example: the same sentence split at a different point by extract_papers.py's
        # paragraph splitter in two different theses -- both fragments must still match.
        text1 = ("others, with whom I have worked at UNSW or elsewhere, is explicitly acknowledged "
                 "in the thesis. I also declare that the intellectual content of this thesis")
        text2 = ("due acknowledgement is made in the thesis. Any contribution made to the research "
                 "by others, with whom I have worked at UNSW or elsewhere, is explicitly acknowledged")
        self.assertIsNotNone(cd.classify_text(text1))
        self.assertIsNotNone(cd.classify_text(text2))

    def test_jstor_footer_boilerplate(self):
        text = ("JSTOR is a not-for-profit service that helps scholars, researchers, and students "
                "discover, use, and build upon a wide range of content in a trusted digital archive.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_bathspa_researchspace_boilerplate(self):
        text = ("ResearchSPAce http://researchspace.bathspa.ac.uk/ This pre-published version is "
                "made available in accordance with publisher policies.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_thesis_contains_published_material_checkbox_boilerplate(self):
        text = ("Please indicate whether this thesis contains published material or not. This thesis "
                "contains no publications, either published or submitted for publication.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_citation_list_lastname_initials_fallback(self):
        text = ("Wu J., Ding Z.Y., Zhang K.C., Improvement of exopolysaccharide production by macro "
                "fungus Auricularia auricula in submerged culture. Enzyme and Microbial Technology.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_citation_list_et_al_no_leading_number_fallback(self):
        # Distinct from the existing "et al" TEXT_PATTERNS entry: the year here follows a title,
        # not "et al" directly, so only the LASTNAME_INITIALS_RE fallback catches it.
        text = ("Law CW, Chen Y, Shi W, et al.: voom: Precision weights unlock linear model analysis "
                "tools for RNA-seq read counts. Genome Biol. 2014; 15(2): R29.")
        self.assertIsNotNone(cd.classify_text(text))

    def test_citation_list_fallback_does_not_misfire_on_short_ordinary_lists(self):
        # Real risk case: ordinary prose listing short all-caps items (state/country codes, acronyms)
        # must NOT be mistaken for a "Lastname Initials," author list.
        texts = [
            "The policy affected the U.S., U.K., and E.U. markets differently, according to the report.",
            "The study included sites in NY, CA, TX, and FL, representing diverse geographic regions.",
            "Participants were drawn from three cohorts: A, B, and C, each recruited from a region.",
        ]
        for text in texts:
            self.assertIsNone(cd.classify_text(text), text)


class TestClassifyTextPatternsOnly(unittest.TestCase):
    """classify_text_patterns_only() -- added for embed_paragraphs.py's pre-embedding boilerplate
    skip (see SKIPPED_MODEL_SENTINEL_BOILERPLATE there). Must agree with classify_text() on the
    curated TEXT_PATTERNS regexes, but must NOT apply classify_text()'s generic fallback
    heuristics (2+ citation years, 2+ emails, leading quote mark) -- those were only ever
    validated against already-paired candidates."""

    def test_matches_curated_pattern_same_as_classify_text(self):
        text = "This work is licensed under a Creative Commons Attribution 4.0 International License."
        self.assertIsNotNone(cd.classify_text_patterns_only(text))
        self.assertEqual(cd.classify_text_patterns_only(text), cd.classify_text(text))

    def test_does_not_apply_year_count_fallback(self):
        text = "Between 1990 and 2005 the field made substantial original progress on this problem."
        self.assertIsNone(cd.classify_text_patterns_only(text))
        # classify_text() itself DOES catch this via the generic fallback -- confirms the two
        # functions genuinely differ, not that the fallback rule stopped firing at all.
        self.assertIsNotNone(cd.classify_text(text))

    def test_does_not_apply_email_count_fallback(self):
        text = "Contact the authors at first.author@example.com or second.author@example.org for details."
        self.assertIsNone(cd.classify_text_patterns_only(text))
        self.assertIsNotNone(cd.classify_text(text))

    def test_does_not_apply_leading_quote_fallback(self):
        text = '"This is an original quoted claim made by this paper itself, not copied from elsewhere."'
        self.assertIsNone(cd.classify_text_patterns_only(text))
        self.assertIsNotNone(cd.classify_text(text))

    def test_ordinary_text_returns_none_for_both(self):
        text = "We propose a novel method for evaluating fairness in machine learning classifiers."
        self.assertIsNone(cd.classify_text_patterns_only(text))
        self.assertIsNone(cd.classify_text(text))


class TestClassifyRowLowOverlap(unittest.TestCase):
    """The near-zero-both-metrics rule added after hand-validating 30 random examples at this
    threshold (todo.md's "another pass" investigation) -- all 30 were coincidental same-topic-
    different-content or non-English embedding noise, not real copying."""

    def test_both_metrics_below_threshold_classified_no(self):
        verdict, reason = cd.classify_row(
            "some completely unrelated paragraph about topic A in one paper",
            "a totally different paragraph about topic B in another paper entirely",
            "Paper One", "Paper Two",
            lcs_ratio=0.02, ngram_jaccard=0.0,
        )
        self.assertEqual(verdict, "no")
        self.assertIn("coincidental embedding-only similarity", reason)

    def test_one_metric_above_threshold_not_classified_by_this_rule(self):
        # lcs_ratio clears the 0.05 floor -- shouldn't fire this specific rule (may still be
        # None from classify_row overall, that's fine, just confirms this isn't the reason).
        verdict, reason = cd.classify_row(
            "some paragraph text here", "some other paragraph text there",
            "Paper One", "Paper Two",
            lcs_ratio=0.10, ngram_jaccard=0.0,
        )
        if reason is not None:
            self.assertNotIn("coincidental embedding-only similarity", reason)

    def test_precomputed_values_used_instead_of_recomputing(self):
        # Deliberately pass precomputed values that contradict what the real text would compute to,
        # to prove classify_row() actually uses the passed-in values rather than recomputing.
        text1 = "identical shared text identical shared text identical shared text"
        text2 = "identical shared text identical shared text identical shared text"
        verdict, reason = cd.classify_row(text1, text2, "Paper One", "Paper Two",
                                           lcs_ratio=0.0, ngram_jaccard=0.0)
        self.assertEqual(verdict, "no")
        self.assertIn("coincidental embedding-only similarity", reason)

    def test_omitted_values_computed_from_text_as_before(self):
        # No lcs_ratio/ngram_jaccard passed -- falls back to computing from the actual text,
        # same behavior as before this parameter existed.
        verdict, reason = cd.classify_row(
            "The quick brown fox jumps over the lazy dog near the riverbank at sunset every single day.",
            "Quantum computing relies on superposition and entanglement to perform calculations beyond classical limits.",
            "Paper One", "Paper Two",
        )
        self.assertEqual(verdict, "no")
        self.assertIn("coincidental embedding-only similarity", reason)


class TestStripTrailingDateBareMonthYear(unittest.TestCase):
    """Regression test for the DOE 'Environmental regulatory update table' cluster: these titles spell
    their date as ', <Month>[-<Month2>] <Year>[. Revision N]' with no parentheses, which the original
    MONTH_YEAR_RE (parenthesized only) and TRAILING_YEAR_RE (bare year only) both missed."""

    def test_collapses_the_real_doe_report_series_titles_to_one_stripped_form(self):
        titles = [
            "Environmental regulatory update table, October 1989",
            "Environmental Regulatory Update Table, December 1991",
            "Environmental regulatory update table, May 1990",
            "Environmental Regulatory Update Table, July--August 1992",
            "Environmental Regulatory Update Table, March/April 1993. Revision 1",
            "Environmental regulatory update table, January 1990",
        ]
        stripped = {cd.strip_trailing_date(t).lower() for t in titles}
        self.assertEqual(len(stripped), 1)

    def test_classify_row_marks_them_as_sequential_editions(self):
        verdict, reason = cd.classify_row(
            "some unrelated body text about topic A",
            "some unrelated body text about topic B",
            "Environmental regulatory update table, January 1990",
            "Environmental Regulatory Update Table, May 1990",
        )
        self.assertEqual(verdict, "no")
        self.assertIn("sequential editions", reason)

    def test_genuinely_different_titles_still_not_collapsed(self):
        self.assertNotEqual(
            cd.strip_trailing_date("Environmental regulatory update table, January 1990").lower(),
            cd.strip_trailing_date("Guide to Industrial Control Systems (ICS) Security").lower(),
        )


if __name__ == "__main__":
    unittest.main()
