# Scoring accuracy, samples, 2026-10-05

Set `samples`: 33 answers to 27 questions, marked by claude; model `thenlper/gte-small`. Fitted and measured on the same answers (in-sample): `tarn score bench` gives the cross-validated difference.
Provisional marks by Claude (D95) from the OCR text against the synthetic seed rubrics: not teacher marks (O29). 33 of 37 answers marked; four came out of segmentation with no text. Answer texts are the segmenter's cut of the cached OCR (P12, page Jaccard 0.84), OCR error about 23 % of characters (P11). Not saved as a calibration.

Bands: ½ credit from 0.850, full from 0.925; borderline margin 0.000; off-target below relevance 0.808 (names: below 0.840).

Flags: 12% of answers flagged; 11% of the answers where AI and teacher differ by more than ½ mark are flagged.

Overall: mean difference 1.167, balanced over the low, middle and high thirds of the marks 1.153. Best constant guesses (the same share of the marks for every answer): 50% by mean difference (1.348; balanced 1.615), 40% by balanced difference (1.527; mean 1.455).

| Question | Answers | Max | Mean abs. diff. (marks) | Within ½ mark | Flagged |
| --- | ---: | ---: | ---: | ---: | ---: |
| A1-Q1 | 2 | 10 | 2.75 | 0% | 0% |
| A1-Q2 | 2 | 10 | 0.75 | 50% | 50% |
| A2-Q1 | 3 | 10 | 1.33 | 33% | 0% |
| QP-CI-Q10 | 1 | 5 | 1.00 | 0% | 0% |
| QP-CI-Q15 | 1 | 10 | 1.50 | 0% | 0% |
| QP-CI-Q16 | 1 | 10 | 1.50 | 0% | 0% |
| QP-CI-Q17 | 2 | 10 | 2.75 | 0% | 0% |
| QP-CI-Q2 | 1 | 2 | 0.00 | 100% | 0% |
| QP-CI-Q3 | 1 | 2 | 0.00 | 100% | 0% |
| QP-CI-Q5 | 1 | 2 | 0.00 | 100% | 0% |
| QP-CI-Q6 | 1 | 2 | 1.00 | 0% | 0% |
| QP-CI-Q7 | 1 | 2 | 0.50 | 100% | 0% |
| QP-CI-Q8 | 1 | 5 | 1.00 | 0% | 0% |
| QP-IPR-Q1 | 1 | 3 | 0.50 | 100% | 0% |
| QP-IPR-Q10 | 2 | 10 | 0.75 | 50% | 0% |
| QP-IPR-Q11 | 2 | 10 | 2.25 | 0% | 0% |
| QP-IPR-Q13A | 1 | 10 | 0.00 | 100% | 0% |
| QP-IPR-Q13B | 1 | 5 | 0.50 | 100% | 0% |
| QP-IPR-Q2 | 1 | 3 | 0.50 | 100% | 0% |
| QP-IPR-Q3 | 2 | 3 | 1.00 | 50% | 100% |
| QP-IPR-Q6 | 1 | 3 | 1.00 | 0% | 0% |
| QP-IPR-Q7 | 1 | 3 | 0.50 | 100% | 0% |
| QP-IPR-Q8 | 2 | 10 | 1.25 | 50% | 50% |
| QP-IPR-Q9 | 1 | 10 | 2.00 | 0% | 0% |
| **all** | 33 | 10 | 1.17 | 42% | 12% |
