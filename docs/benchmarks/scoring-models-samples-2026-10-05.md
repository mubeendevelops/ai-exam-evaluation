# Scoring models, 2026-10-05

Set `samples`: 33 answers to 27 questions, marked by claude. Models run on the CPU from local files.
Cross-validated: the bands are fitted with each group of questions held out (5 groups) and measured on it; fitted: fitted and measured on every answer. Balanced MAE: the mean of the mean absolute differences of the low, middle and high thirds of the marks (the fitting objective); models are ranked by its cross-validated value.
Sample booklets: 33 OCR-read handwritten answers (9 booklets, 27 questions) with provisional marks by Claude (D95) against the synthetic seed rubrics; far too few to choose a model on, shown as a check on handwriting with ~23 % OCR character error.

Best constant guesses (the same share of the marks for every answer): 50% by mean difference (1.348; balanced 1.615), 40% by balanced difference (1.527; mean 1.455).

| Model | Answers | Spearman (similarity vs mark) | Balanced MAE, cross-validated | MAE, cross-validated | Balanced MAE, fitted | Bands (½, 1) | s / answer |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| trigram | 33 | 0.423 | 1.132 | 1.106 | 1.071 | 0.275, 0.575 | 0.059 |
| thenlper/gte-small | 33 | 0.500 | 1.153 | 1.167 | 1.153 | 0.850, 0.925 | 0.870 |
| BAAI/bge-small-en-v1.5 | 33 | 0.452 | 1.336 | 1.348 | 1.075 | 0.650, 0.850 | 0.259 |
| intfloat/e5-small-v2 | 33 | 0.518 | 1.421 | 1.379 | 1.238 | 0.850, 0.950 | 0.268 |
| sentence-transformers/all-MiniLM-L12-v2 | 33 | 0.470 | 1.600 | 1.485 | 1.247 | 0.375, 0.675 | 0.260 |
| sentence-transformers/all-MiniLM-L6-v2 | 33 | 0.424 | 1.682 | 1.576 | 1.312 | 0.275, 0.825 | 0.142 |
| sentence-transformers/all-mpnet-base-v2 | 33 | 0.525 | 1.733 | 1.561 | 1.326 | 0.375, 0.850 | 0.841 |
