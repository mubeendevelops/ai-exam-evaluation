# Scoring accuracy, mohler, 2026-10-05

Set `mohler`: 2273 answers to 81 questions, marked by dataset; model `thenlper/gte-small`. Fitted and measured on the same answers (in-sample): `tarn score bench` gives the cross-validated difference.
Public set: UNT short-answer grading data v2.0 (Mohler, Bunescu and Mihalcea, ACL 2011): computer-science questions, typed answers, the average of two human graders on 0-5; one semantic criterion per instructor-answer sentence. Bands fitted on the balanced difference (skewed marks: mean 4.18 of 5). Off-target relevance thresholds are the 2nd and 10th percentiles of the answers' relevance (no off-target answers are labelled in this set). Saved as calibration gte-small v2 (v1 had the relevance check fitted away).

Bands: ½ credit from 0.750, full from 0.900; borderline margin 0.020; off-target below relevance 0.795 (names: below 0.834).

Flags: 39% of answers flagged; 43% of the answers where AI and teacher differ by more than ½ mark are flagged.

Overall: mean difference 1.117, balanced over the low, middle and high thirds of the marks 0.919. Best constant guesses (the same share of the marks for every answer): 90% by mean difference (0.809; balanced 1.971), 50% by balanced difference (1.341; mean 1.832).

| Question | Answers | Max | Mean abs. diff. (marks) | Within ½ mark | Flagged |
| --- | ---: | ---: | ---: | ---: | ---: |
| M-1.1 | 29 | 5 | 0.60 | 72% | 17% |
| M-1.2 | 29 | 5 | 1.21 | 31% | 38% |
| M-1.3 | 29 | 5 | 1.24 | 17% | 34% |
| M-1.4 | 29 | 5 | 1.90 | 45% | 38% |
| M-1.5 | 29 | 5 | 0.81 | 55% | 31% |
| M-1.6 | 29 | 5 | 0.81 | 59% | 41% |
| M-1.7 | 29 | 5 | 0.55 | 72% | 45% |
| M-10.1 | 24 | 5 | 0.73 | 58% | 25% |
| M-10.2 | 24 | 5 | 1.23 | 33% | 25% |
| M-10.3 | 24 | 5 | 0.46 | 75% | 17% |
| M-10.4 | 24 | 5 | 0.27 | 92% | 12% |
| M-10.5 | 24 | 5 | 0.60 | 67% | 29% |
| M-10.6 | 24 | 5 | 0.81 | 58% | 50% |
| M-10.7 | 24 | 5 | 2.25 | 12% | 46% |
| M-11.1 | 30 | 5 | 0.78 | 57% | 20% |
| M-11.10 | 30 | 5 | 0.37 | 80% | 30% |
| M-11.2 | 30 | 5 | 1.12 | 63% | 20% |
| M-11.3 | 30 | 5 | 0.78 | 57% | 47% |
| M-11.4 | 30 | 5 | 1.38 | 40% | 43% |
| M-11.5 | 30 | 5 | 0.88 | 60% | 60% |
| M-11.6 | 30 | 5 | 2.28 | 33% | 67% |
| M-11.7 | 30 | 5 | 1.22 | 50% | 50% |
| M-11.8 | 30 | 5 | 0.68 | 70% | 40% |
| M-11.9 | 30 | 5 | 1.37 | 33% | 40% |
| M-12.1 | 28 | 5 | 1.54 | 36% | 68% |
| M-12.10 | 28 | 5 | 2.00 | 21% | 29% |
| M-12.2 | 28 | 5 | 1.16 | 39% | 64% |
| M-12.4 | 28 | 5 | 1.23 | 46% | 57% |
| M-12.5 | 28 | 5 | 0.82 | 68% | 54% |
| M-12.6 | 28 | 5 | 1.23 | 14% | 71% |
| M-12.7 | 28 | 5 | 0.82 | 75% | 29% |
| M-12.8 | 28 | 5 | 0.84 | 50% | 46% |
| M-12.9 | 28 | 5 | 0.95 | 43% | 54% |
| M-2.1 | 30 | 5 | 0.82 | 67% | 33% |
| M-2.2 | 30 | 5 | 0.73 | 63% | 60% |
| M-2.3 | 30 | 5 | 0.45 | 73% | 47% |
| M-2.4 | 30 | 5 | 0.90 | 53% | 50% |
| M-2.5 | 30 | 5 | 1.97 | 23% | 33% |
| M-2.6 | 30 | 5 | 0.98 | 43% | 67% |
| M-2.7 | 30 | 5 | 1.02 | 37% | 7% |
| M-3.1 | 31 | 5 | 0.47 | 87% | 35% |
| M-3.2 | 31 | 5 | 1.58 | 35% | 13% |
| M-3.3 | 31 | 5 | 1.00 | 58% | 39% |
| M-3.4 | 31 | 5 | 1.27 | 29% | 32% |
| M-3.5 | 31 | 5 | 1.69 | 29% | 32% |
| M-3.6 | 31 | 5 | 0.97 | 55% | 39% |
| M-3.7 | 31 | 5 | 0.35 | 81% | 35% |
| M-4.1 | 30 | 5 | 1.18 | 43% | 37% |
| M-4.2 | 30 | 5 | 0.67 | 53% | 40% |
| M-4.3 | 30 | 5 | 2.35 | 20% | 40% |
| M-4.4 | 30 | 5 | 0.55 | 73% | 40% |
| M-4.5 | 30 | 5 | 1.22 | 40% | 30% |
| M-5.1 | 28 | 5 | 1.14 | 46% | 71% |
| M-5.2 | 28 | 5 | 1.57 | 25% | 39% |
| M-5.3 | 28 | 5 | 1.52 | 32% | 32% |
| M-5.4 | 28 | 5 | 1.57 | 36% | 25% |
| M-6.1 | 26 | 5 | 0.88 | 62% | 31% |
| M-6.2 | 26 | 5 | 0.90 | 58% | 15% |
| M-6.3 | 26 | 5 | 1.65 | 12% | 42% |
| M-6.4 | 26 | 5 | 1.71 | 15% | 38% |
| M-6.5 | 26 | 5 | 1.02 | 46% | 54% |
| M-6.6 | 26 | 5 | 0.17 | 92% | 15% |
| M-6.7 | 26 | 5 | 1.04 | 50% | 54% |
| M-7.1 | 26 | 5 | 1.87 | 4% | 4% |
| M-7.2 | 26 | 5 | 1.33 | 46% | 46% |
| M-7.3 | 26 | 5 | 0.83 | 58% | 46% |
| M-7.4 | 26 | 5 | 1.52 | 27% | 12% |
| M-7.5 | 26 | 5 | 0.29 | 85% | 27% |
| M-7.6 | 26 | 5 | 1.13 | 12% | 15% |
| M-7.7 | 26 | 5 | 1.62 | 35% | 15% |
| M-8.1 | 27 | 5 | 0.98 | 63% | 52% |
| M-8.2 | 27 | 5 | 0.83 | 67% | 33% |
| M-8.3 | 27 | 5 | 1.24 | 37% | 56% |
| M-8.4 | 27 | 5 | 1.06 | 37% | 56% |
| M-8.6 | 27 | 5 | 0.33 | 78% | 37% |
| M-8.7 | 27 | 5 | 0.72 | 63% | 37% |
| M-9.1 | 27 | 5 | 2.11 | 11% | 37% |
| M-9.2 | 27 | 5 | 0.46 | 89% | 22% |
| M-9.3 | 27 | 5 | 1.26 | 33% | 41% |
| M-9.4 | 27 | 5 | 1.15 | 41% | 44% |
| M-9.6 | 27 | 5 | 3.46 | 15% | 74% |
| **all** | 2273 | 5 | 1.12 | 48% | 39% |
