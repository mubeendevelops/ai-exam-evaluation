# Scoring models, 2026-10-07

Set `mohler`: 2273 answers to 81 questions, marked by dataset. Models run on the CPU from local files.
Public set: UNT short-answer grading data v2.0 (Mohler, Bunescu and Mihalcea, ACL 2011), 81 computer-science questions, 2,273 typed answers, the average of two human graders on 0-5. Each instructor-answer sentence is one semantic criterion (even weights). The marks are skewed high (mean 4.18 of 5; 80 answers in the low third, 370 in the middle, 1,823 in the high), which is why the balanced difference is the measure.
Cross-validated: the bands are fitted with each group of questions held out (5 groups) and measured on it; fitted: fitted and measured on every answer. Balanced MAE: the mean of the mean absolute differences of the low, middle and high thirds of the marks (the fitting objective); models are ranked by its cross-validated value.

Best constant guesses (the same share of the marks for every answer): 90% by mean difference (0.809; balanced 1.971), 50% by balanced difference (1.341; mean 1.832).

| Model | Answers | Spearman (similarity vs mark) | Balanced MAE, cross-validated | MAE, cross-validated | Balanced MAE, fitted | Bands (½, 1) | s / answer |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| thenlper/gte-small | 2273 | 0.543 | 0.920 | 1.117 | 0.919 | 0.750, 0.900 | 0.027 |
| trigram | 2273 | 0.538 | 0.935 | 1.029 | 0.925 | 0.000, 0.400 | 0.001 |
| BAAI/bge-small-en-v1.5 | 2273 | 0.545 | 0.935 | 1.136 | 0.935 | 0.000, 0.800 | 0.016 |
| intfloat/e5-small-v2 | 2273 | 0.535 | 0.956 | 1.197 | 0.956 | 0.000, 0.900 | 0.017 |
| sentence-transformers/all-MiniLM-L12-v2 | 2273 | 0.521 | 0.960 | 1.143 | 0.939 | 0.050, 0.650 | 0.016 |
| sentence-transformers/all-mpnet-base-v2 | 2273 | 0.469 | 0.963 | 1.169 | 0.948 | 0.000, 0.675 | 0.047 |
| sentence-transformers/all-MiniLM-L6-v2 | 2273 | 0.499 | 0.976 | 1.161 | 0.952 | 0.050, 0.625 | 0.009 |
