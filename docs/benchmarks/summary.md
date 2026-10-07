# Benchmark summary — 2026-10-07

Measured error rates of the four machine stages, from the P22 re-run on the development laptop (GTX 1650 4 GB, CPU engines on the host). Numbers only: no text, page image or student identifier is in this file or in the reports it links to.

**How to read these numbers.** Every set is small or borrowed, and every threshold and weight in the product is still a placeholder until teachers mark real booklets. The rates describe *this* material; they are not promises about a college's booklets. The re-run gave the same figures as the first measurements (OCR 4 Oct, segmentation 4 Oct, scoring 5 Oct, diagrams 6 Oct); only timings moved by noise. Nothing was refitted and no calibration was saved to the database.

| Stage | Set | Headline | Report |
| --- | --- | --- | --- |
| OCR (best of N) | 267 hand-verified lines, 12 pages, 9 booklets, one transcriber | selector CER **22.8 %**, WER **58.4 %**; best single engine (TrOCR) CER 27.4 % | [`ocr-2026-10-07.md`](ocr-2026-10-07.md) |
| Segmentation | 9 sample booklets, truth labelled by Claude (not a teacher) | page Jaccard **0.84**; answer starts precision 73 % / recall 68 %; 4 of 9 orders exact | [`segmentation-2026-10-07.md`](segmentation-2026-10-07.md) |
| Text scoring | Mohler public set, 2,273 typed answers (computer science) | mean difference **1.12 marks of 5** (balanced 0.92; best constant guess 1.34); within ½ mark 48 % | [`scoring-mohler-2026-10-07.md`](scoring-mohler-2026-10-07.md), [`scoring-models-2026-10-07.md`](scoring-models-2026-10-07.md) |
| Text scoring | 33 handwritten sample answers, marks provisional by Claude (O29) | mean difference 1.17 (balanced 1.15); within ½ mark 42 % | [`scoring-samples-2026-10-07.md`](scoring-samples-2026-10-07.md) |
| Diagrams | public flowchart sets (FC, Flowchart 3b) + synthetic, test splits | shapes **98–100 %** precision and recall; FC edges **96–97 %** precision, **95–96 %** recall | [`diagram-detector-2026-10-07.md`](diagram-detector-2026-10-07.md) |

## OCR (P10–P11)

267 verified lines (handwriting only; 266 cursive, 1 numeric; 88 scanning-app, 94 clean-scan, 85 phone-photo lines). CER = character errors ÷ characters of the truth; WER the same over words.

| Method | CER | 95 % interval | WER |
| --- | ---: | --- | ---: |
| Tesseract | 78.8 % | 70.8 – 84.4 % | 117.3 % |
| PaddleOCR | 30.4 % | 22.1 – 39.3 % | 72.1 % |
| TrOCR (handwritten) | 27.4 % | 20.2 – 35.5 % | 65.0 % |
| **Selector (best of N, production)** | **22.8 %** | 16.2 – 30.2 % | 58.4 % |
| Selector, calibrated (5-fold by page) | 24.2 % | – | 59.9 % |
| Oracle (best engine per line, upper bound) | 20.3 % | 13.9 – 27.3 % | 55.2 % |

- The selector beats the best single engine by **4.6 points** of CER (95 % interval 3.1 to 6.0 points, bootstrap over pages): the case for best-of-N (R4). It sits 2.5 points above the oracle.
- By capture: scanning app 17.8 %, phone photo 19.4 %, clean scan 29.0 %.
- Removing an engine: without TrOCR 30.4 %, without Paddle 27.4 %, without Tesseract 22.8 % (Tesseract earns nothing on handwriting; it is kept for print).
- Fitting per-engine calibrations did not help yet (24.2 % against 22.8 %): too few lines per engine and class (30 needed).
- The flag threshold 0.6 marks 20.6 % of lines; the flagged lines have CER 46 % against 16 % for the rest.
- Line detection covers 95.9 % of the truth lines (an upper bound: the truth boxes start from the detector's own).
- **Time per page** (12 pages, warm): GPU 12.3 s in total (layout 2.4, Paddle 3.9, Tesseract 3.0, TrOCR 3.1); CPU-only 30.8 s (TrOCR 21.6 s of it). This is the production-compute question (O100): on the CPU a 12-page booklet takes about six minutes to read.
- Not measured: print and numeric lines (none or one), the cloud engines (off), more than one transcriber.

## Segmentation (P12)

Nine booklets cut against their seeded papers (QP-CI, QP-IPR, assignments) and compared with a labelling that Claude made from the page images; a person has not checked it (O54).

| Embedder | Orders exact | Start precision | Start recall | Continuations | Page Jaccard |
| --- | ---: | ---: | ---: | ---: | ---: |
| all-MiniLM-L6-v2 (production) | 4 / 9 | 73 % | 68 % | 84 % | **0.84** |
| trigram (no model) | 3 / 9 | 69 % | 62 % | 67 % | 0.68 |

- The assignment booklets (b-su1–3, b-el2) are exact; the QP-IPR booklets are close (page Jaccard 0.93 and 0.81).
- The QP-CI booklets score worst (0.61 and 0.49): the OCR rarely reads their margin labels (O55), so the decoder leans on similarity, and 6 blocks of one booklet end up in the tray for the teacher.
- Weights are hand-set; fitting them on six labelled booklets did worse out of sample (0.55 against 0.75), so they stay (D88–D92).
- Page reordering is tested on synthetic pages only: no sample has pages out of order.

## Text scoring (P13)

Mean difference between the machine's mark and the human mark, in marks (Mohler questions are out of 5; the sample answers out of 2 to 10). The set's marks are skewed high, so the **balanced** difference (mean over the low, middle and high thirds of the marks) is the measure the bands are fitted on (D99).

| Set | Answers | Mean difference | Balanced | Best constant guess (balanced) | Within ½ mark | Answers flagged | Disagreements flagged |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Mohler (typed CS answers, 2 human graders) | 2,273 | 1.12 | 0.92 | 1.34 | 48 % | 39 % | 43 % |
| Samples (handwritten, marks by Claude) | 33 | 1.17 | 1.15 | 1.53 | 42 % | 12 % | 11 % |

Both are in-sample fits of the bands; the models report (cross-validated, 5 groups of questions held out) gives the honest figure for Mohler:

| Model | Spearman (similarity vs mark) | Balanced difference, cross-validated | Seconds per answer |
| --- | ---: | ---: | ---: |
| **thenlper/gte-small (production)** | 0.543 | **0.920** | 0.027 |
| trigram (no model) | 0.538 | 0.935 | 0.001 |
| bge-small-en-v1.5 | 0.545 | 0.935 | 0.016 |
| e5-small-v2 | 0.535 | 0.956 | 0.017 |
| all-MiniLM-L12-v2 | 0.521 | 0.960 | 0.016 |
| all-mpnet-base-v2 | 0.469 | 0.963 | 0.047 |
| all-MiniLM-L6-v2 | 0.499 | 0.976 | 0.009 |

- The machine is better than guessing a constant, but a typical suggestion is **about one mark off on a 5-mark answer**, and fewer than half the suggestions fall within half a mark. That is why the teacher decides every mark and the suggestion is shown with per-criterion reasons.
- Word overlap alone comes close to the embedding models (trigram 0.935 against 0.920): the models add little over simple matching on this set (O62).
- The flags catch fewer than half the disagreements (43 % on Mohler, 11 % on the samples): do not rely on a clear flag to mean "right" (O61).
- The calibration comes from typed computer-science answers and is applied to handwritten law and civics answers read through an OCR with 23 % character errors: there is **no teacher-marked handwritten set**. Refit with `tarn score calibrate SET --save` once teachers have marked booklets (O60).
- The LLM second opinion (off by default) was called live only on synthetic answers; its agreement with teachers is unknown (O29).

## Diagram recognition and comparison (P14)

`shape-detector-v1` (RT-DETR R18, 7 classes), threshold 0.35, IoU ≥ 0.5, test splits only (never seen in training; the FC split is by writer).

| Test set | Images | Shapes: precision / recall | Edges with direction: precision / recall | Edges ignoring direction: precision / recall |
| --- | ---: | --- | --- | --- |
| FC offline scans | 140 | 98.0 % / 98.0 % | 96.6 % / 96.1 % | 100 % / 99.5 % |
| FC skeletons | 140 | 98.0 % / 98.0 % | 96.1 % / 95.2 % | 100 % / 99.1 % |
| Flowchart 3b (photos of hand-drawn flowcharts) | 155 | 99.7 % / 99.7 % | (heads placed by direction, not annotated) | – |
| Synthetic trees, networks, block diagrams | 300 | 100 % / 100 % | 64.2 % / 70.9 % | 85.0 % / 94.0 % |

- About 0.1 s per image on the GTX 1650 (0.34 GB peak).
- The synthetic drawings are mostly lines without heads, so direction is arbitrary there: read the "ignoring direction" columns; they are harder than the public sets on purpose.
- **No handwritten engineering diagram from a real booklet was available (C24).** The three public sets are flowcharts. Two computer-made block diagrams and one hand-drawn photo were checked by hand (every box and arrow found; one arrow head missed on the photo).
- The graph comparison (similarity, missing, extra, reversed, disconnected) is tested on synthetic graphs (`core/tests/test_diagram_compare.py`); its weights and acceptance threshold are placeholders (O69).

## What a college should take from this

1. OCR reads handwriting with about one wrong character in four to five. A teacher still corrects lines in step 2, and each correction is kept as ground truth for the next measurement.
2. Segmentation is right where labels are legible and unsure where they are not; unsure text goes to the tray rather than being guessed.
3. Scoring suggestions are a first pass, about one mark off on a five-mark answer on a public typed set; real handwritten teacher-marked data will decide whether that is good enough.
4. Diagram recognition is accurate on public flowcharts; engineering diagrams and handwritten booklet diagrams are untested.

## Re-running

From `backend/` with the dev stack's data (`../var/` is git-ignored and holds the sets):

```bash
uv run tarn bench ocr ../var/groundtruth/dev
uv run tarn bench segment ../var/segmentation
uv run tarn score calibrate ../var/scoring/mohler      # also: ../var/scoring/samples
uv run tarn score bench ../var/scoring/mohler
uv run tarn diagram bench
```

Each writes `docs/benchmarks/<name>-<date>.md`. The OCR run needs the models in `var/models/` (`tarn ocr models fetch`) and takes about ten minutes with the CPU timing pass; `score bench` about eight. `score calibrate --save` stores a calibration in the database: leave it off unless the set is teacher-marked.
