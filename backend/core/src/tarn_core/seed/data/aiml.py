"""K-AI1, K-AI2 and K-AI3: the faculty keys for AI & ML (22CS53), CIA 2 and CIA 3.

Marks and step marks are the ones printed in the keys. Where a key is inconsistent, the
choice made is written in the question's note (the keys number their rows loosely, and several
answers are images: their content is written out here in words). The numbers (sums, centroids,
sigmoids, regression) were recomputed. Diagrams are cut from the keys by
``scripts/extract_seed_diagrams.sh``."""

from tarn_core.domain.content import Difficulty
from tarn_core.domain.diagram import DiagramKind
from tarn_core.seed.model import (
    DiagramSeed,
    QuestionSeed,
    SubjectSeed,
    dia_parts,
    guidance,
    key,
    lst,
    num,
    question,
    sem,
    synthetic,
    term,
)

SUBJECT = SubjectSeed(code="22CS53", name="Artificial Intelligence & Machine Learning")
CLUSTERING = "Clustering"
NEURAL = "Neural networks"
ML = "Machine learning"
AGENTS = "Intelligent agents"
LOGIC = "Logic"

EASY, MEDIUM, HARD = Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD

# ---- K-AI1 (CIA 2) --------------------------------------------------------------------------

K_AI1: tuple[QuestionSeed, ...] = (
    question(
        "K-AI1-Q1",
        "Elaborate Balanced Iterative Reducing using Clustering Hierarchies algorithm",
        10,
        CLUSTERING,
        [
            key(
                "BIRCH (Balanced Iterative Reducing and Clustering Using Hierarchies) "
                "incrementally constructs a CF (Clustering Feature) tree, a hierarchical data "
                "structure for multiphase clustering.\n"
                "Phase 1: scan the database to build an initial in-memory CF tree (a "
                "multi-level compression of the data that tries to preserve its inherent "
                "clustering structure).\n"
                "Phase 2: use an arbitrary clustering algorithm to cluster the leaf nodes of "
                "the CF tree.\n"
                "It scales linearly: it finds a good clustering with a single scan and improves "
                "the quality with a few additional scans. Weakness: it handles only numeric "
                "data and is sensitive to the order of the data records. [5 marks]\n"
                "Clustering Feature CF = (N, LS, SS): N is the number of data points, LS the "
                "linear sum of the N points and SS their square sum. A CF tree has two "
                "parameters: the branching factor (maximum number of children) and the "
                "threshold (maximum diameter of the sub-clusters stored at the leaf nodes). "
                "[5 marks]"
            )
        ],
        [
            sem(
                "Overview: incremental CF tree for multiphase clustering",
                1,
                "BIRCH incrementally constructs a clustering feature (CF) tree, a hierarchical "
                "data structure, for multiphase clustering.",
            ),
            sem(
                "Phase 1: build the in-memory CF tree",
                1.5,
                "In phase 1 the database is scanned once to build an initial in-memory CF tree, "
                "a multi-level compression of the data that preserves its clustering structure.",
            ),
            sem(
                "Phase 2: cluster the leaf nodes",
                1.5,
                "In phase 2 an arbitrary clustering algorithm is used to cluster the leaf nodes "
                "of the CF tree.",
            ),
            sem(
                "Scalability and weakness",
                1,
                "BIRCH scales linearly with a single scan; it handles only numeric data and is "
                "sensitive to the order of the data records.",
            ),
            sem(
                "Clustering feature CF = (N, LS, SS)",
                3,
                "A clustering feature is the triple CF = (N, LS, SS): N is the number of data "
                "points, LS is the linear sum of the points and SS is their square sum.",
            ),
            lst(
                "CF tree parameters",
                2,
                [
                    term("branching factor", "maximum number of children"),
                    term("threshold", "maximum diameter"),
                ],
            ),
        ],
        terms=["CF tree", "clustering feature", "branching factor", "threshold", "leaf node"],
    ),
    question(
        "K-AI1-Q2A",
        "Describe in detail a UNIT, the basic building block of a neural network.",
        5,
        NEURAL,
        [
            key(
                "A unit is the basic building block of a neural network (Figure: inputs x1..xn "
                "with weights w1..wn, the products of inputs and weights, a function f with a "
                "bias b, and the output f(sum of x_i * w_i + b)). [3 marks for the diagram]\n"
                "1. A unit is a function that takes as input a vector x in R^n and produces a "
                "scalar.\n"
                "2. A unit is parameterised by a weight vector w in R^n and a bias term b.\n"
                "3. The output of the unit is f(sum of x_i * w_i + b), where f: R -> R is the "
                "activation function.\n"
                "4. A variety of activation functions may be used; generally it is a non-linear "
                "function. [2 marks]"
            )
        ],
        [
            *dia_parts("Unit diagram", "unit", 1, 1, 1),
            sem(
                "Input vector, weights and bias",
                1,
                "A unit takes an input vector x and produces a scalar; it is parameterised by a "
                "weight vector w and a bias term b.",
            ),
            sem(
                "Output through the activation function",
                1,
                "The output of the unit is f of the weighted sum of the inputs plus the bias, "
                "where f is an activation function, generally non-linear.",
            ),
        ],
        terms=["weights", "bias", "activation function", "weighted sum"],
        diagrams=[DiagramSeed("unit", "k-ai1-q2a-unit.png", DiagramKind.NETWORK)],
    ),
    question(
        "K-AI1-Q2B",
        "Given the neural network shown in the diagram with two input neurons (x1=0.35, x2=0.7), "
        "two hidden neurons (h1, h2), and one output neuron (O3), calculate the final output "
        "value Y using the sigmoid activation function at each hidden neuron and the output "
        "neuron.\nWeights and bias as given in the key: x1->h1 0.2, x2->h1 0.2, x1->h2 -0.3, "
        "x2->h2 0.3, h1->O3 0.3, h2->O3 0.9; bias b = 0.2.",
        5,
        NEURAL,
        [
            key(
                "Hidden neuron h1: z = 0.35*0.2 + 0.7*0.2 + 0.2 = 0.41; h1 = sigmoid(0.41) = "
                "1/(1+e^-0.41) = 0.6010878788.\n"
                "Hidden neuron h2: z = 0.35*(-0.3) + 0.7*0.3 + 0.2 = 0.305; h2 = sigmoid(0.305) "
                "= 0.5756643510. [3 marks]\n"
                "Output neuron O3: z = h1*0.3 + h2*0.9 + 0.2 = 0.1803263637 + 0.5180979159 + "
                "0.2 = 0.8984242796; Y = sigmoid(0.8984242796) = 0.71062558... "
                "The final output Y = 0.71063. [2 marks]"
            )
        ],
        [
            num("Hidden neuron h1: weighted sum", 0.5, 0.41, 0.001),
            num("Hidden neuron h1: sigmoid output", 1, 0.6011, 0.001),
            num("Hidden neuron h2: weighted sum", 0.5, 0.305, 0.001),
            num("Hidden neuron h2: sigmoid output", 1, 0.5757, 0.001),
            num("Output neuron: weighted sum", 1, 0.8984, 0.001),
            num("Final output Y", 1, 0.7106, 0.001),
        ],
        difficulty=EASY,
        diagrams=[DiagramSeed("network", "k-ai1-q2b-network.png", DiagramKind.NETWORK)],
    ),
    question(
        "K-AI1-Q3",
        "Given the 2-D dataset: X = {P1(1,1), P2(1,2), P3(2,1), P4(8,8), P5(9,8), P6(8,9)}, use "
        "k-means with k=2. Start with initial centroids c1=P1(1,1) and c2=P5(9,8) respectively. "
        "Perform 2 iterations of k-means and show assignments and centroid updates.",
        10,
        CLUSTERING,
        [
            key(
                "Iteration 1: centroids c1 = (1,1), c2 = (9,8). Distances: P1 to c1 0.000, to c2 "
                "10.630 -> c1; P2: 1.000, 10.000 -> c1; P3: 1.000, 9.899 -> c1; P4: 9.899, 1.000 "
                "-> c2; P5: 10.630, 0.000 -> c2; P6: 10.630, 1.414 -> c2. Clusters: c1 = {P1, "
                "P2, P3}, c2 = {P4, P5, P6}. New c1 = (4/3, 4/3) = (1.3333, 1.3333); new c2 = "
                "(25/3, 25/3) = (8.3333, 8.3333). [5 marks]\n"
                "Iteration 2: centroids c1 = (1.3333, 1.3333), c2 = (8.3333, 8.3333). P1: d to "
                "c1 0.471, to c2 10.371 -> c1; P2: 0.745, 9.690 -> c1; P3: 0.745, 9.690 -> c1; "
                "P4: 9.428, 0.471 -> c2; P5: 10.160, 0.745 -> c2; P6: 10.160, 0.745 -> c2. The "
                "assignments did not change and the centroids are the same: converged. Final "
                "clusters: {P1, P2, P3} with centroid (1.3333, 1.3333) and {P4, P5, P6} with "
                "centroid (8.3333, 8.3333). [5 marks]"
            )
        ],
        [
            sem(
                "Iteration 1: distances and assignment",
                2,
                "In iteration 1, P1, P2 and P3 are nearest to c1 = (1,1) and P4, P5 and P6 are "
                "nearest to c2 = (9,8), so the clusters are {P1, P2, P3} and {P4, P5, P6}.",
            ),
            num("Iteration 1: new centroid c1 (each coordinate)", 1.5, 1.3333, 0.001),
            num("Iteration 1: new centroid c2 (each coordinate)", 1.5, 8.3333, 0.001),
            num("Iteration 2: distance of P1 to c1", 1, 0.471, 0.001),
            sem(
                "Iteration 2: assignment unchanged",
                2,
                "In iteration 2 the assignments are the same as in iteration 1 and the "
                "centroids do not change.",
            ),
            sem(
                "Conclusion: converged clusters",
                2,
                "After two iterations the k-means clustering has converged: cluster 1 is "
                "P1, P2, P3 with centroid (1.3333, 1.3333) and cluster 2 is P4, P5, P6 with "
                "centroid (8.3333, 8.3333).",
            ),
        ],
        difficulty=MEDIUM,
        terms=["centroid", "Euclidean distance", "assignment", "convergence"],
    ),
    question(
        "K-AI1-Q4",
        "Consider the 1-D dataset: {1, 2, 3, 8, 9, 10, 25}. Three possible clustering partitions "
        "are given as: Clustering (i): C1 = {1, 2, 3}, C2 = {8, 9, 10, 25}; Clustering (ii): C1 "
        "= {1, 2, 3, 8}, C2 = {9, 10, 25}; Clustering (iii): C1 = {1, 2}, C2 = {3, 8, 9, 10, "
        "25}. Analyze and compare the above clustering solutions using within cluster variation. "
        "Identify the best clustering among the three and justify your choice.",
        10,
        CLUSTERING,
        [
            key(
                "Clustering (i): mean(C1) = 2, WCV(C1) = 1 + 0 + 1 = 2; mean(C2) = 13, WCV(C2) = "
                "25 + 16 + 9 + 144 = 194; total WCV(i) = 196. [2 marks]\n"
                "Clustering (ii): mean(C1) = 3.5, WCV(C1) = 6.25 + 2.25 + 0.25 + 20.25 = 29; "
                "mean(C2) = 14.67, WCV(C2) = 32.11 + 21.78 + 106.78 = 160.67; total WCV(ii) = "
                "189.67. [2 marks]\n"
                "Clustering (iii): mean(C1) = 1.5, WCV(C1) = 0.5; mean(C2) = 11, WCV(C2) = 64 + "
                "9 + 4 + 1 + 196 = 274; total WCV(iii) = 274.5. [2 marks]\n"
                "Comparison: WCV(i) = 196, WCV(ii) = 189.67 (lowest), WCV(iii) = 274.5 "
                "(highest). [2 marks]\n"
                "Best clustering = (ii), because it has the minimum within-cluster variation, "
                "meaning its clusters are more compact around their centroids than (i) and "
                "(iii). [2 marks]"
            )
        ],
        [
            num("Total WCV of clustering (i)", 2, 196, 0.5),
            num("Total WCV of clustering (ii)", 2, 189.67, 0.05),
            num("Total WCV of clustering (iii)", 2, 274.5, 0.5),
            sem(
                "Comparison of the three values",
                2,
                "Clustering (ii) has the lowest within-cluster variation, 189.67, and "
                "clustering (iii) the highest, 274.5.",
            ),
            sem(
                "Best clustering and justification",
                2,
                "Clustering (ii) is best because it has the minimum within-cluster variation, "
                "so its clusters are the most compact around their centroids.",
            ),
        ],
        terms=["within-cluster variation", "WCV", "mean", "compact"],
    ),
    question(
        "K-AI1-Q5",
        "Consider the following six 2-D data points: Q1(1,1), Q2(1,2), Q3(2,1), Q4(8,8), Q5(9,8), "
        "Q6(8,9). Perform AGNES using single linkage and draw the dendrogram for the clustering "
        "process.",
        10,
        CLUSTERING,
        [
            key(
                "Step 1: Q1 (1,1) and Q2 (1,2) merge at distance 1.0 -> {Q1, Q2}. Step 2: {Q1, "
                "Q2} and Q3 (2,1) at 1.0 -> {Q1, Q2, Q3}. Step 3: Q4 (8,8) and Q5 (9,8) at 1.0 "
                "-> {Q4, Q5}. Step 4: {Q4, Q5} and Q6 (8,9) at 1.0 -> {Q4, Q5, Q6}. Step 5: "
                "{Q1, Q2, Q3} and {Q4, Q5, Q6} at 9.22 -> all six points. [6 marks]\n"
                "Dendrogram: the left cluster {Q1, Q2, Q3} and the right cluster {Q4, Q5, Q6} "
                "are each formed at height 1 and joined at a much larger distance (about "
                "9.22). [2 marks]\n"
                "Conclusion: AGNES with single linkage groups the data into two natural "
                "clusters {Q1, Q2, Q3} and {Q4, Q5, Q6}. [2 marks]\n"
                "Note: the printed key shows 6 + 2 + 3 marks for these parts; the seed uses "
                "6 + 2 + 2 so that the question carries 10 (teacher to confirm)."
            )
        ],
        [
            sem(
                "Merge steps with single linkage",
                4,
                "Single linkage merges Q1 with Q2, then Q3, and Q4 with Q5, then Q6, each at "
                "distance 1.0, giving the clusters {Q1, Q2, Q3} and {Q4, Q5, Q6}.",
            ),
            num("Distance of the final merge", 2, 9.22, 0.01),
            *dia_parts("Dendrogram", "dendrogram", 0.5, 1, 0.5),
            sem(
                "Conclusion: two natural clusters",
                2,
                "The dendrogram shows two natural clusters, {Q1, Q2, Q3} and {Q4, Q5, Q6}, "
                "joined only at a much larger distance.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["AGNES", "single linkage", "dendrogram", "agglomerative"],
        diagrams=[DiagramSeed("dendrogram", "k-ai1-q5-dendrogram.png", DiagramKind.PLOT)],
    ),
    question(
        "K-AI1-Q6A",
        "Write an algorithm for Density-Based Spatial Clustering of Applications with Noise "
        "(DBSCAN).",
        10,
        CLUSTERING,
        [
            key(
                "Algorithm DBSCAN: a density-based clustering algorithm. Input: D, a data set "
                "containing n objects; epsilon, the radius parameter; MinPts, the neighbourhood "
                "density threshold. Output: a set of density-based clusters. [2 marks]\n"
                "Method: (1) mark all objects as unvisited; (2) do; (3) randomly select an "
                "unvisited object p; (4) mark p as visited; (5) if the epsilon-neighbourhood of "
                "p has at least MinPts objects; (6) create a new cluster C, and add p to C; (7) "
                "let N be the set of objects in the epsilon-neighbourhood of p; [4 marks] (8) "
                "for each point p' in N; (9) if p' is unvisited; (10) mark p' as visited; (11) "
                "if the epsilon-neighbourhood of p' has at least MinPts points, add those "
                "points to N; (12) if p' is not yet a member of any cluster, add p' to C; (13) "
                "end for; (14) output C; (15) else mark p as noise; (16) until no object is "
                "unvisited. [4 marks]"
            )
        ],
        [
            lst(
                "Inputs and output",
                2,
                [
                    term("data set D", "dataset", "n objects"),
                    term("epsilon", "eps", "radius"),
                    term("MinPts", "minimum points", "density threshold"),
                    term("clusters", "density-based clusters"),
                ],
                3,
            ),
            sem(
                "Select a point; start a cluster or mark noise",
                4,
                "Pick an unvisited object p and mark it visited. If the epsilon-neighbourhood "
                "of p has at least MinPts objects, create a new cluster C containing p and let "
                "N be the neighbourhood of p; otherwise mark p as noise.",
            ),
            sem(
                "Expand the cluster until nothing is unvisited",
                4,
                "For each unvisited point p' in N mark it visited and, if its neighbourhood has "
                "at least MinPts points, add those points to N; add p' to C if it is not in any "
                "cluster; output C and repeat until no object is unvisited.",
            ),
        ],
        difficulty=HARD,
        terms=["DBSCAN", "epsilon neighbourhood", "MinPts", "noise", "core point"],
    ),
    question(
        "K-AI1-Q6B",
        "CF1 has points (1,2),(3,4),(5,6) and CF2 has points (30,40),(25,35),(12,16),(14,17). "
        "Apply the additive rule of Clustering Features (CFs) on CF1 and CF2 to find CF3.",
        2,
        CLUSTERING,
        [
            key(
                "A clustering feature of N d-dimensional points is CF = (N, LS, SS) with LS the "
                "linear sum and SS the sum of squares. Additive rule: CF3 = CF1 + CF2 = (N1 + "
                "N2, LS1 + LS2, SS1 + SS2). [1 mark]\n"
                "CF1 = (3, (9, 12), (35, 56)); CF2 = (4, (81, 108), (1865, 3370)). CF3 = (7, "
                "(90, 120), (1900, 3426)). [1 mark]\n"
                "Note: the printed key gives 1 + 1 marks for this part, with none for the "
                "intermediate sums."
            )
        ],
        [
            sem(
                "Additive rule",
                1,
                "The additive rule adds the clustering features component by component: "
                "CF3 = (N1 + N2, LS1 + LS2, SS1 + SS2).",
            ),
            lst(
                "CF3 = (7, (90, 120), (1900, 3426))",
                1,
                [
                    term("N = 7", "7"),
                    term("LS = (90, 120)", "(90, 120)", "90, 120"),
                    term("SS = (1900, 3426)", "(1900, 3426)", "1900, 3426"),
                ],
            ),
        ],
        difficulty=EASY,
        terms=["clustering feature", "additive rule", "linear sum", "square sum"],
    ),
    question(
        "K-AI1-Q7A",
        "Express the neural network in vector form with a suitable diagram.",
        5,
        NEURAL,
        [
            key(
                "Assume the input is x in R^n and the first layer has p1 units; each unit has "
                "weights w in R^n, so the weights of the first layer form a matrix W1 in "
                "R^(n x p1), with a bias vector b1 in R^p1. The first layer produces a vector "
                "o1 = f(x*W1 + b1) in R^p1, where f is applied element by element. The second "
                "layer, with p2 units, takes o1 and produces o2 = f(o1*W2 + b2) in R^p2. The "
                "entire computation up to layer 2 is f(f(x*W1 + b1)*W2 + b2). [2 marks]\n"
                "Diagram: input x in R^n, weight matrix W1 (n x p1) giving o1 in R^p1, then "
                "weight matrix W2 (p1 x p2) giving o2 in R^p2. [3 marks]"
            )
        ],
        [
            sem(
                "First layer in vector form",
                1,
                "The first layer output is o1 = f(x W1 + b1), with x in R^n, a weight matrix W1 "
                "of size n by p1 and a bias vector, giving a vector in R^p1.",
            ),
            sem(
                "Second layer and the whole network",
                1,
                "The second layer gives o2 = f(o1 W2 + b2) in R^p2, so the network computes "
                "f(f(x W1 + b1) W2 + b2).",
            ),
            *dia_parts("Vector-form diagram", "vector-form", 1, 1, 1),
        ],
        terms=["weight matrix", "bias vector", "layer", "vectorised"],
        diagrams=[DiagramSeed("vector-form", "k-ai1-q7a-vector-form.png", DiagramKind.NETWORK)],
    ),
    question(
        "K-AI1-Q7B",
        "Apply Max pool and Avg-pool (2 x 2 blocks, stride 2) for the following feature map:\n"
        "74 44 56 78\n80 40 43 13\n78 88 90 96\n70 55 80 35",
        5,
        NEURAL,
        [
            key(
                "Max pooling takes the maximum of each 2 x 2 block: max(74, 44, 80, 40) = 80; "
                "max(56, 78, 43, 13) = 78; max(78, 88, 70, 55) = 88; max(90, 96, 80, 35) = 96. "
                "Max-pooled output = [80 78 88 96]. [2.5 marks]\n"
                "Average pooling takes the average of each block: (74+44+80+40)/4 = 59.5; "
                "(56+78+43+13)/4 = 47.5; (78+88+70+55)/4 = 72.75; (90+96+80+35)/4 = 75.25. "
                "Avg-pooled output = [59.5 47.5 72.75 75.25]. [2.5 marks]"
            )
        ],
        [
            lst(
                "Max pooling result",
                2.5,
                [term("80"), term("78"), term("88"), term("96")],
            ),
            lst(
                "Average pooling result",
                2.5,
                [term("59.5"), term("47.5"), term("72.75"), term("75.25")],
            ),
        ],
        difficulty=EASY,
        terms=["max pooling", "average pooling", "feature map", "stride"],
    ),
    question(
        "K-AI1-Q8A",
        "Elaborate Simple Recurrent Neural Network with a suitable diagram.",
        5,
        NEURAL,
        [
            key(
                "A simple RNN takes a sequence of inputs x(1), ..., x(tau). It can produce an "
                "output y(t) for every entry of the input sequence or a single output for the "
                "whole sequence; the target is y(t) and the output the RNN produces is "
                "y-hat(t). It is described by the equations h(t) = tanh(U x(t) + W h(t-1) + b) "
                "and y-hat(t) = softmax(V h(t) + c). [2 marks]\n"
                "Diagram (Figure 6-1, recurrence using the previous hidden state): the input "
                "x(t) feeds the hidden state h(t) = tanh(U x(t) + W h(t-1) + b), which takes "
                "the previous hidden state through the weights W and feeds the output y-hat(t) "
                "= softmax(V h(t) + c); the loss L(t) compares y-hat(t) with y(t), for t = "
                "1..tau. [3 marks]"
            )
        ],
        [
            sem(
                "A sequence in, an output per step or one in the end",
                1,
                "A recurrent network takes a sequence of inputs x(1) to x(tau) and produces an "
                "output for every entry or a single output for the whole sequence.",
            ),
            sem(
                "Equations of the simple RNN",
                1,
                "The hidden state is h(t) = tanh(U x(t) + W h(t-1) + b) and the output is "
                "softmax(V h(t) + c).",
            ),
            *dia_parts("RNN diagram", "rnn", 1, 1, 1),
        ],
        difficulty=MEDIUM,
        terms=["recurrent", "hidden state", "tanh", "softmax", "time step"],
        diagrams=[DiagramSeed("rnn", "k-ai1-q8a-rnn.png", DiagramKind.NETWORK)],
    ),
    question(
        "K-AI1-Q8B",
        "Perform a convolution operation on a 5x5 matrix using a 3x3 kernel. Provide the "
        "numerical steps and the final output matrix.\n"
        "A = [[1,-1,1,-1,1], [-1,1,-1,1,-1], [1,1,-1,-1,1], [-1,-1,1,1,-1], [1,-1,-1,1,1]]; "
        "kernel = [[1,0,-1], [1,0,-1], [1,0,-1]] (stride 1, no padding).",
        3,
        NEURAL,
        [
            key(
                "Each output element is the sum of the element-wise products of the kernel with "
                "the 3 x 3 sub-matrix of A. C11 = 2, C12 = 2, C13 = -2, C21 = 0, C22 = 0, C23 = "
                "0, C31 = 2, C32 = -2, C33 = -2. The output matrix C = [[2, 2, -2], [0, 0, 0], "
                "[2, -2, -2]]. [2 marks for the first four elements, 1 mark for the rest]\n"
                "Note: the printed key shows only 2 + 1 marks for this part."
            )
        ],
        [
            lst(
                "C11, C12, C13 and C21",
                2,
                [
                    term("C11 = 2", "C(1,1) = 2"),
                    term("C12 = 2", "C(1,2) = 2"),
                    term("C13 = -2", "C(1,3) = -2"),
                    term("C21 = 0", "C(2,1) = 0"),
                ],
            ),
            lst(
                "C22, C23, C31, C32 and C33",
                1,
                [
                    term("C22 = 0", "C(2,2) = 0"),
                    term("C23 = 0", "C(2,3) = 0"),
                    term("C31 = 2", "C(3,1) = 2"),
                    term("C32 = -2", "C(3,2) = -2"),
                    term("C33 = -2", "C(3,3) = -2"),
                ],
            ),
        ],
        difficulty=MEDIUM,
        terms=["convolution", "kernel", "stride", "feature map"],
    ),
)

# ---- K-AI2 (header blank) -------------------------------------------------------------------

K_AI2: tuple[QuestionSeed, ...] = (
    question(
        "K-AI2-Q1",
        "Define Machine Learning. Differentiate between supervised, unsupervised, and "
        "reinforcement learning with suitable real-world examples.",
        10,
        ML,
        [
            key(
                "Machine Learning algorithms enable computers to learn from data, and even "
                "improve themselves, without being explicitly programmed. Reasons for ML: rapid "
                "increment in the production of data, solving complex problems which are "
                "difficult for a human, decision making in various sectors including finance, "
                "and finding hidden patterns and extracting useful information from data. ML "
                "types: supervised, unsupervised, reinforcement. [4 marks] The key then says "
                "only: 'Difference with examples of each' [3 * 2 = 6 marks]."
            ),
            synthetic(
                "Supervised learning learns from labelled examples (input with the right "
                "output) to predict the output for new inputs, for example classifying emails "
                "as spam or not spam, or predicting house prices. Unsupervised learning finds "
                "structure in unlabelled data, for example clustering customers into segments "
                "or finding associations in shopping baskets. Reinforcement learning has an "
                "agent that learns by trial and error from rewards and penalties from its "
                "environment, for example a robot learning to walk or a program learning to "
                "play chess."
            ),
        ],
        [
            sem(
                "Definition of machine learning",
                2,
                "Machine learning algorithms enable computers to learn from data and improve "
                "themselves without being explicitly programmed.",
            ),
            sem(
                "Why machine learning is needed",
                2,
                "ML is needed because of the rapid growth of data, to solve complex problems "
                "that are difficult for a human, to support decision making, and to find hidden "
                "patterns and useful information in data.",
            ),
            sem(
                "Supervised learning with an example",
                2,
                "Supervised learning learns from labelled examples to predict outputs for new "
                "inputs, for example spam classification or price prediction.",
            ),
            sem(
                "Unsupervised learning with an example",
                2,
                "Unsupervised learning finds structure in unlabelled data, for example "
                "clustering customers into segments.",
            ),
            sem(
                "Reinforcement learning with an example",
                2,
                "Reinforcement learning has an agent that learns by trial and error from "
                "rewards and penalties from its environment, for example a robot learning to "
                "walk.",
            ),
        ],
        difficulty=EASY,
        terms=["labelled data", "unlabelled data", "reward", "agent", "classification"],
    ),
    question(
        "K-AI2-Q2",
        "A company wants to predict the salary of employees based on their years of experience. "
        "The HR manager has collected the following dataset (years of experience, salary): "
        "(1, 32383), (1.1, 45207), (1.3, 39751), (2, 43525), (2.2, 39891), (2.7, 56642), "
        "(3, 60150). Write the formula and calculate: (i) estimate the values of beta0 and "
        "beta1 for the linear regression model that best fits the given data; (ii) predict the "
        "salary of a new employee with 3.7 years of experience.",
        10,
        ML,
        [
            key(
                "beta0 (intercept) = 25,839.87 and beta1 (slope) = 10,275.93 [6 marks]; Y = "
                "25839.87 + 10275.93 * X [1 mark]. For a new employee with 3.7 years of "
                "experience the predicted salary is Y = 25839.87 + 10275.93 * 3.7 = 63,860.82 "
                "[3 marks]."
            )
        ],
        [
            num("Slope beta1", 3, 10275.93, 0.5),
            num("Intercept beta0", 3, 25839.87, 0.5),
            sem(
                "Regression equation",
                1,
                "The fitted line is Y = beta0 + beta1 X, that is Y = 25839.87 + 10275.93 X.",
            ),
            num("Predicted salary for 3.7 years", 3, 63860.82, 1),
        ],
        difficulty=MEDIUM,
        terms=["least squares", "slope", "intercept", "prediction"],
    ),
    question(
        "K-AI2-Q3",
        "Write the pseudocode for the ID3 algorithm to build a decision tree for classification.",
        10,
        ML,
        [
            key(
                "Algorithm Generate_decision_tree: generate a decision tree from the training "
                "tuples of data partition D. Input: data partition D, a set of training tuples "
                "and their associated class labels; attribute_list, the set of candidate "
                "attributes; Attribute_selection_method, a procedure to determine the splitting "
                "criterion that best partitions the tuples into individual classes. Output: a "
                "decision tree. [3 marks]\n"
                "Method: (1) create a node N; (2) if tuples in D are all of the same class C, "
                "then (3) return N as a leaf node labelled with the class C; (4) if "
                "attribute_list is empty then (5) return N as a leaf node labelled with the "
                "majority class in D; (6) apply Attribute_selection_method(D, attribute_list) "
                "to find the best splitting_criterion; (7) label node N with the "
                "splitting_criterion; (8) if splitting_attribute is discrete-valued and "
                "multiway splits are allowed then (9) attribute_list <- attribute_list - "
                "splitting_attribute; (10) for each outcome j of splitting_criterion: (11) let "
                "Dj be the set of data tuples in D satisfying outcome j; (12) if Dj is empty "
                "then (13) attach a leaf labelled with the majority class in D to node N; (14) "
                "else attach the node returned by Generate_decision_tree(Dj, attribute_list) to "
                "node N; endfor; (15) return N. [7 marks]"
            )
        ],
        [
            lst(
                "Input and output of the algorithm",
                3,
                [
                    term("data partition D", "training tuples", "dataset"),
                    term("attribute list", "candidate attributes"),
                    term("attribute selection method", "splitting criterion"),
                    term("decision tree", "output tree"),
                ],
                3,
            ),
            sem(
                "Create a node; leaf if one class",
                1.5,
                "Create a node N; if all tuples in D are of the same class, return N as a leaf "
                "labelled with that class.",
            ),
            sem(
                "Leaf with the majority class if no attributes remain",
                1.5,
                "If the attribute list is empty, return N as a leaf labelled with the majority "
                "class in D.",
            ),
            sem(
                "Choose the best splitting attribute",
                1.5,
                "Apply the attribute selection method to find the best splitting criterion and "
                "label node N with it, removing the attribute from the list for multiway "
                "splits.",
            ),
            sem(
                "Partition and handle an empty partition",
                1.5,
                "For each outcome of the splitting criterion form the partition Dj; if it is "
                "empty attach a leaf with the majority class of D.",
            ),
            sem(
                "Recurse on each partition and return the node",
                1,
                "Otherwise attach the subtree returned by calling the algorithm recursively on "
                "Dj, then return N.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["ID3", "decision tree", "information gain", "majority class", "leaf node"],
    ),
    question(
        "K-AI2-Q4",
        "Explain any two attribute selection methods in detail: 1. Information Gain 2. Gain "
        "Ratio 3. Gini Index",
        10,
        ML,
        [
            guidance(
                "Student has to explain why the measure is used, which algorithm uses it, the "
                "formula to calculate the attribute measure along with an example, and how the "
                "selection process happens. [2 * 5 = 10 marks] The key gives no model answer: "
                "mark by hand."
            )
        ],
        difficulty=MEDIUM,
        terms=["information gain", "gain ratio", "Gini index", "entropy"],
    ),
    question(
        "K-AI2-Q5",
        "A company is trying to classify whether an email is Spam or Not Spam. Consider the "
        "following set of training examples. [The training table is not part of the faculty "
        "key.] Students have to write formulas step by step and calculate the entropy of the "
        "class distribution and the information gain of attribute a2.",
        10,
        ML,
        [
            key(
                "i) Overall class distribution: 2 '+' and 4 '-'. H(S) = -(2/6)log2(2/6) - "
                "(4/6)log2(4/6) = 0.918 bits. [4 marks]\n"
                "ii) Split on a2: a2 = T has instances {1, 2, 5, 6}: 2 '+', 2 '-', H = 1 bit; "
                "a2 = F has instances {3, 4}: 0 '+', 2 '-', H = 0 bits. Conditional entropy "
                "H(S|a2) = (4/6)(1) + (2/6)(0) = 2/3 = 0.667. Information gain Gain(S, a2) = "
                "H(S) - H(S|a2) = 0.918 - 0.667 = 0.252 bits. [6 marks]"
            )
        ],
        [
            sem(
                "Entropy formula and class distribution",
                2,
                "The class distribution is 2 positive and 4 negative examples and the entropy "
                "is H(S) = minus the sum over classes of p log2 p.",
            ),
            num("Entropy H(S)", 2, 0.918, 0.001, "bits"),
            sem(
                "Split on a2: the two branches",
                2,
                "For a2 = T there are 4 instances, 2 positive and 2 negative, with entropy 1 "
                "bit; for a2 = F there are 2 instances, both negative, with entropy 0 bits.",
            ),
            num("Conditional entropy H(S|a2)", 2, 0.667, 0.001, "bits"),
            num("Information gain of a2", 2, 0.252, 0.001, "bits"),
        ],
        difficulty=HARD,
        terms=["entropy", "information gain", "conditional entropy", "log2"],
    ),
    question(
        "K-AI2-Q6",
        "A zoologist is building a model to classify animals. Use the Naive Bayes Classifier to "
        "classify the following new instance: Color = Green, legs = 2, Height = Tall, and "
        "Smelly = No. [The training table (8 animals, classes M and H) is not part of the "
        "faculty key.] Students have to write formulas step by step and calculate.",
        10,
        ML,
        [
            key(
                "There are 8 training examples: P(M) = 4/8 = 0.5 and P(H) = 4/8 = 0.5. [1 mark]\n"
                "Class M: P(Green|M) = 0.5, P(legs=2|M) = 0.25, P(Tall|M) = 0.25, P(Smelly=No|M)"
                " = 0.25. Likelihood P(x|M) = 0.5 * 0.25 * 0.25 * 0.25 = 0.0078125. Posterior "
                "numerator P(M) P(x|M) = 0.00390625. [4 marks]\n"
                "Class H: P(Green|H) = 0.25, P(legs=2|H) = 1, P(Tall|H) = 0.5, P(Smelly=No|H) = "
                "0.75. Likelihood P(x|H) = 0.25 * 1 * 0.5 * 0.75 = 0.09375. Posterior numerator "
                "P(H) P(x|H) = 0.046875. [4 marks]\n"
                "Normalise: denominator = 0.00390625 + 0.046875 = 0.05078125; P(M|x) = 1/13 = "
                "0.0769 and P(H|x) = 12/13 = 0.9231. Prediction: classify the new instance as "
                "species H, since P(H|x) is larger. [1 mark]"
            )
        ],
        [
            num("Prior probabilities P(M) = P(H)", 1, 0.5, 0),
            num("Likelihood P(x|M)", 2, 0.0078125, 0.000001),
            num("Posterior numerator for M", 2, 0.00390625, 0.000001),
            num("Likelihood P(x|H)", 2, 0.09375, 0.00001),
            num("Posterior numerator for H", 2, 0.046875, 0.00001),
            sem(
                "Prediction",
                1,
                "The posterior of class H, about 0.923, is larger than that of class M, about "
                "0.077, so the new instance is classified as H.",
            ),
        ],
        difficulty=HARD,
        terms=["Naive Bayes", "prior", "likelihood", "posterior", "conditional independence"],
    ),
    question(
        "K-AI2-Q7",
        "Write short notes on: a) RSS b) Hypothesis Testing c) R2 d) SE",
        10,
        ML,
        [
            key(
                "a) RSS (residual sum of squares): the total deviation of the observed values "
                "from the values predicted by a regression model; RSS = sum of (y_i - "
                "y-hat_i)^2; a smaller RSS indicates a better fit; used to assess accuracy and "
                "performance.\n"
                "b) Hypothesis testing: a statistical method used to make decisions about a "
                "population based on sample data; it formulates the null hypothesis H0 (no "
                "effect or difference) and the alternative hypothesis H1; steps: define H0 and "
                "H1, choose the significance level alpha, compute the test statistic, compare "
                "with the critical value or p-value and decide; common tests: t-test, "
                "chi-square, ANOVA, z-test.\n"
                "c) R2 (coefficient of determination): the proportion of the variance in the "
                "dependent variable explained by the independent variable(s); R2 = 1 - RSS/TSS "
                "with TSS the total sum of squares; between 0 and 1 (0: explains nothing, 1: "
                "perfect); higher generally means a better model, but very high values may "
                "indicate overfitting.\n"
                "d) SE (standard error): a measure of the variability or precision of a sample "
                "statistic; for the mean SE = s / sqrt(n); a smaller SE means a more precise "
                "estimate; used in hypothesis testing and confidence intervals. [4 * 2.5 = 10 "
                "marks]"
            )
        ],
        [
            sem(
                "RSS",
                2.5,
                "RSS, the residual sum of squares, is the sum of squared differences between "
                "observed and predicted values; a smaller RSS means a better fit.",
            ),
            sem(
                "Hypothesis testing",
                2.5,
                "Hypothesis testing uses sample data to decide about a population by comparing "
                "a null hypothesis with an alternative hypothesis using a test statistic and a "
                "significance level.",
            ),
            sem(
                "R squared",
                2.5,
                "R squared is the proportion of the variance of the dependent variable "
                "explained by the model, 1 minus RSS over TSS, between 0 and 1.",
            ),
            sem(
                "Standard error",
                2.5,
                "The standard error measures the precision of a sample statistic; for the mean "
                "it is s divided by the square root of n.",
            ),
        ],
        difficulty=EASY,
        terms=["RSS", "null hypothesis", "R squared", "standard error", "p-value"],
    ),
    question(
        "K-AI2-Q8",
        "Explain simple linear regression. How are the co-efficients estimated? Students have "
        "to explain with an example.",
        10,
        ML,
        [
            key(
                "Simple linear regression is a statistical technique used to model the "
                "relationship between a dependent variable (response) Y and one independent "
                "variable (predictor) X. The model assumes a linear relationship Y = beta0 + "
                "beta1 X + epsilon, where beta0 is the intercept (value of Y when X = 0), beta1 "
                "is the slope (change in Y for a one-unit change in X) and epsilon is the error "
                "term (unexplained variation). [4 marks]\n"
                "Estimation of the coefficients: beta0 and beta1 are usually estimated using "
                "the least squares method, which minimises the sum of squared residuals RSS = "
                "sum of (y_i - y-hat_i)^2. Slope: beta1-hat = sum of (x_i - x-bar)(y_i - "
                "y-bar) / sum of (x_i - x-bar)^2. Intercept: beta0-hat = y-bar - beta1-hat * "
                "x-bar. [5 marks]\n"
                "Example: students explain with an example [1 mark]."
            )
        ],
        [
            sem(
                "Definition of simple linear regression",
                2,
                "Simple linear regression models the relationship between one dependent "
                "variable Y and one independent variable X.",
            ),
            sem(
                "Model equation and its terms",
                2,
                "The model is Y = beta0 + beta1 X + epsilon, with beta0 the intercept, beta1 "
                "the slope and epsilon the error term.",
            ),
            sem(
                "Least squares estimation",
                2,
                "The coefficients are estimated by the least squares method, which minimises "
                "the residual sum of squares.",
            ),
            sem(
                "Slope formula",
                1.5,
                "The slope is the sum of (x minus mean x) times (y minus mean y) divided by the "
                "sum of (x minus mean x) squared.",
            ),
            sem(
                "Intercept formula",
                1.5,
                "The intercept is the mean of y minus the slope times the mean of x.",
            ),
            sem(
                "Example",
                1,
                "An example such as predicting salary from years of experience, fitting a line "
                "and using it to predict.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["least squares", "slope", "intercept", "residual", "dependent variable"],
    ),
)

# ---- K-AI3 (CIA 3) --------------------------------------------------------------------------

K_AI3: tuple[QuestionSeed, ...] = (
    question(
        "K-AI3-Q1",
        "Consider a Smart Home Cleaning Robot as an intelligent agent. Identify its performance "
        "measure, environment, sensors, and actuators, and briefly explain how the percept "
        "sequence and corresponding actions enable the components of a Smart Home Cleaning "
        "Robot to work together and achieve intelligent cleaning behavior.",
        10,
        AGENTS,
        [
            key(
                "Performance measure: area cleaned, minimum time taken, avoiding collisions "
                "with furniture or people, efficient battery usage, returning to the charging "
                "station when needed. Environment: partially observable, dynamic, continuous "
                "and stochastic: rooms with varying floor surfaces (tiles, carpet, wood), "
                "furniture, pets, humans and obstacles, changing dirt conditions. Sensors: "
                "infrared/ultrasonic sensors to detect obstacles, camera or LIDAR to map "
                "rooms, dust or dirt sensor, wheel rotation sensors, battery level sensor. "
                "Actuators: motorised wheels, brush and vacuum assembly, suction motor, "
                "charging port actuator for docking. Percept to action: dust detected -> "
                "increase suction and clean; obstacle in front -> turn and choose a new path; "
                "battery low -> navigate to the charging dock. [4 * 2 = 8 marks, plus 2 marks "
                "for the percept-action explanation]\n"
                "Note: the printed key shows 4x2 = 8 marks, then 3M and 2M beside the sensors "
                "and percept parts; the seed uses 2 marks for each of the five parts."
            )
        ],
        [
            lst(
                "Performance measure",
                2,
                [
                    term("area cleaned", "clean area", "coverage"),
                    term("time", "minimum time", "speed"),
                    term("collisions", "avoid obstacles", "safety"),
                    term("battery", "energy"),
                    term("charging station", "docking"),
                ],
                3,
            ),
            lst(
                "Environment",
                2,
                [
                    term("partially observable"),
                    term("dynamic"),
                    term("continuous"),
                    term("stochastic"),
                    term("furniture", "obstacles", "pets"),
                    term("floor surfaces", "tiles", "carpet"),
                ],
                4,
            ),
            lst(
                "Sensors",
                2,
                [
                    term("infrared", "ultrasonic", "obstacle sensor"),
                    term("camera", "LIDAR"),
                    term("dust sensor", "dirt sensor"),
                    term("wheel rotation", "odometry", "encoder"),
                    term("battery level", "battery sensor"),
                ],
                3,
            ),
            lst(
                "Actuators",
                2,
                [
                    term("wheels", "motor"),
                    term("brush"),
                    term("vacuum", "suction"),
                    term("charging port", "docking"),
                ],
                3,
            ),
            sem(
                "Percept sequence and actions",
                2,
                "The percepts from the sensors, such as dust detected, an obstacle in front or "
                "low battery, are mapped to actions such as cleaning harder, turning to a new "
                "path or going to the charging dock, so the sensors, decision rules and "
                "actuators work together.",
            ),
        ],
        difficulty=EASY,
        terms=["PEAS", "percept", "sensor", "actuator", "performance measure"],
    ),
    question(
        "K-AI3-Q2",
        "Given the following set of predicate expressions involving the predicate Teaches, "
        "perform the unification operation for each pair. Write the resulting substitutions "
        "and explain why unification fails in any case. i. UNIFY(Teaches(John, x), "
        "Teaches(John, Math)) ii. UNIFY(Teaches(John, x), Teaches(y, Physics)) iii. "
        "UNIFY(Teaches(John, x), Teaches(y, Subject(y))) iv. UNIFY(Teaches(John, x), "
        "Teaches(x, Alice))",
        10,
        LOGIC,
        [
            key(
                "i. John matches John; x is unified with Math: theta = {x -> Math}. [2 marks]\n"
                "ii. John vs y gives y -> John; x vs Physics gives x -> Physics: theta = "
                "{y -> John, x -> Physics}. [2 marks]\n"
                "iii. y -> John; Subject(y) becomes Subject(John); x -> Subject(John); no "
                "variable occurs inside its own substitution: theta = {y -> John, x -> "
                "Subject(John)}. [2 marks]\n"
                "iv. John vs x requires x -> John; the second pair then becomes John vs Alice, "
                "two different constants that cannot be made equal: unification fails. "
                "[4 marks]"
            )
        ],
        [
            sem(
                "i. Unifier {x -> Math}",
                2,
                "Unification succeeds with the substitution x maps to Math.",
            ),
            sem(
                "ii. Unifier {y -> John, x -> Physics}",
                2,
                "Unification succeeds with the substitution y maps to John and x maps to Physics.",
            ),
            sem(
                "iii. Unifier {y -> John, x -> Subject(John)}",
                2,
                "Unification succeeds with y mapped to John and x mapped to Subject(John), "
                "because Subject(y) becomes Subject(John).",
            ),
            sem(
                "iv. Unification fails",
                2,
                "Unification fails: x would have to be John, and then the second argument "
                "compares the constants John and Alice, which are different.",
            ),
            sem(
                "iv. Reason for the failure",
                2,
                "Two different constants cannot be made equal by any substitution, so no "
                "substitution makes both positions identical.",
            ),
        ],
        difficulty=HARD,
        terms=["unification", "substitution", "constant", "variable", "predicate"],
    ),
    question(
        "K-AI3-Q3A",
        "Given the world of Boolean variables p, q, r and the knowledge base KB: i. p -> q ii. "
        "r -> q iii. q = F. Show that KB entails {p = F, q = F, r = F}.",
        4,
        LOGIC,
        [
            key(
                "KB: 1. p -> q; 2. r -> q; 3. q = F. From (3) q is false. p -> q is equivalent "
                "to (not p) or q; with q false it can only be true when not p is true, so p "
                "must be false. The same argument applied to r -> q gives r false. Therefore "
                "every model of KB assigns p = F, q = F, r = F, so KB entails {p = F, q = F, "
                "r = F}. [4 marks]"
            )
        ],
        [
            sem("q is false", 1, "From the third sentence q is false."),
            sem(
                "p must be false",
                1.5,
                "p implies q is equivalent to not p or q; with q false this is true only if p "
                "is false.",
            ),
            sem(
                "r must be false",
                1,
                "In the same way r implies q with q false forces r to be false.",
            ),
            sem(
                "Conclusion",
                0.5,
                "Every model of the knowledge base has p, q and r false, so KB entails them.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["entailment", "implication", "knowledge base", "model"],
    ),
    question(
        "K-AI3-Q3B",
        "Describe the properties of task environments that influence the design and performance "
        "of an intelligent agent.",
        6,
        AGENTS,
        [
            key(
                "1. Fully observable (vs partially observable): the sensors give access to the "
                "complete state of the environment; partially observable example: vacuum agent, "
                "automated taxi. 2. Deterministic (vs stochastic): the next state is completely "
                "determined by the current state and the action; deterministic: vacuum world; "
                "stochastic: automated taxi; if deterministic except for other agents the "
                "environment is strategic. 3. Episodic (vs sequential): experience is divided "
                "into atomic episodes; episodic: detecting defective parts on an assembly line; "
                "sequential: chess, taxi driving. 4. Static (vs dynamic): the environment does "
                "not change while the agent deliberates; semi-dynamic if only the performance "
                "score changes with time; dynamic: taxi driving; static: crossword puzzle; "
                "semi-dynamic: chess with a clock. 5. Discrete (vs continuous): a limited number "
                "of distinct percepts and actions; discrete: chess; continuous: taxi driving. "
                "6. Single agent (vs multi-agent): an agent operating by itself; single agent: "
                "crossword puzzle; multi-agent: chess, taxi (competitive, cooperative). "
                "[6 * 1 = 6 marks]"
            )
        ],
        [
            sem(
                "Observable or partially observable",
                1,
                "In a fully observable environment the sensors give the complete state; "
                "otherwise it is partially observable, as for a taxi.",
            ),
            sem(
                "Deterministic or stochastic",
                1,
                "In a deterministic environment the next state is fully determined by the "
                "current state and the action; otherwise it is stochastic.",
            ),
            sem(
                "Episodic or sequential",
                1,
                "In an episodic environment the experience divides into atomic episodes that "
                "do not depend on each other; in a sequential one earlier actions matter.",
            ),
            sem(
                "Static or dynamic",
                1,
                "A static environment does not change while the agent deliberates; a dynamic "
                "one does.",
            ),
            sem(
                "Discrete or continuous",
                1,
                "A discrete environment has a limited number of distinct percepts and actions; "
                "a continuous one does not.",
            ),
            sem(
                "Single agent or multi-agent",
                1,
                "A single-agent environment has one agent acting alone; a multi-agent one has "
                "several agents that compete or cooperate.",
            ),
        ],
        difficulty=EASY,
        terms=["task environment", "observable", "deterministic", "episodic", "multi-agent"],
    ),
    question(
        "K-AI3-Q4",
        "Write an agent program for a model-based agent and a utility-based agent. Explain with "
        "justification which of these agents performs better, supporting your answer with a "
        "suitable example.",
        10,
        AGENTS,
        [
            key(
                "Model-based reflex agent: function MODEL-BASED-REFLEX-AGENT(percept) returns "
                "an action; persistent: state, the agent's current conception of the world "
                "state; model, a description of how the next state depends on the current "
                "state and action; rules, a set of condition-action rules; action, the most "
                "recent action, initially none. state <- UPDATE-STATE(state, action, percept, "
                "model); rule <- RULE-MATCH(state, rules); action <- rule.ACTION; return "
                "action. It keeps track of the current state of the world using an internal "
                "model, then chooses an action in the same way as the reflex agent. [4 marks "
                "with the diagram]\n"
                "Utility-based agent: the agent program uses the model to predict what the "
                "world will be like if it does an action, and a utility function that measures "
                "how happy it will be in such a state, then chooses the action with the "
                "highest expected utility (diagram: state, how the world evolves, what my "
                "actions do, utility, what action I should do now). [2 marks]\n"
                "Which performs better: the utility-based agent, [2 marks] because it takes "
                "into consideration client feedback and happiness [2 marks]. Note: the printed "
                "justification is brief and contestable; a teacher should confirm it."
            )
        ],
        [
            sem(
                "Model-based agent program",
                2,
                "The model-based reflex agent keeps an internal state, updates it from the "
                "percept, the last action and a model of how the world works, matches a "
                "condition-action rule to the state and returns the rule's action.",
            ),
            *dia_parts("Model-based agent diagram", "model-based", 0.5, 1, 0.5),
            sem(
                "Utility-based agent program",
                1,
                "The utility-based agent predicts the outcome of each action with its model and "
                "chooses the action that leads to the state with the highest utility.",
            ),
            *dia_parts("Utility-based agent diagram", "utility-based", 0.25, 0.5, 0.25),
            sem(
                "Which agent performs better",
                2,
                "The utility-based agent performs better because it compares how desirable the "
                "resulting states are rather than only following fixed condition-action rules.",
            ),
            sem(
                "Justification with an example",
                2,
                "The utility-based agent takes into consideration feedback and happiness: for "
                "example a taxi choosing between routes weighs speed, safety and passenger "
                "comfort, which condition-action rules alone cannot trade off.",
            ),
        ],
        difficulty=HARD,
        terms=["model-based agent", "utility function", "condition-action rule", "internal state"],
        diagrams=[
            DiagramSeed("model-based", "k-ai3-q4-model-based-agent.png", DiagramKind.BLOCK),
            DiagramSeed("utility-based", "k-ai3-q4-utility-based-agent.png", DiagramKind.BLOCK),
        ],
    ),
    question(
        "K-AI3-Q5",
        "Convert the following English statements into First-Order Logic (FOL) representations "
        "by identifying appropriate predicates, constants, and quantifiers: i. Tom is a doctor, "
        "Jerry is a patient, and all doctors treat patients. ii. All humans are mortal, "
        "Socrates is a human, therefore Socrates is mortal. iii. Every student has a teacher, "
        "and all teachers are knowledgeable. iv. Every cat chases some mouse. v. All cars have "
        "engines, and every electric car has a battery.",
        10,
        LOGIC,
        [
            key(
                "i) Doctor(Tom) and Patient(Jerry) and for all x (Doctor(x) -> exists y "
                "(Patient(y) and Treats(x,y))). ii) (for all x (Human(x) -> Mortal(x))) and "
                "Human(Socrates); Mortal(Socrates) follows. iii) for all s (Student(s) -> "
                "exists t (Teacher(t) and Teaches(t,s))) and for all t (Teacher(t) -> "
                "Knowledgeable(t)). iv) for all x (Cat(x) -> exists y (Mouse(y) and "
                "Chases(x,y))). v) as objects: for all x (Car(x) -> exists e (Engine(e) and "
                "HasEngine(x,e))) and for all x (ElectricCar(x) -> exists b (Battery(b) and "
                "HasBattery(x,b))); or as properties: for all x (Car(x) -> HasEngine(x)) and "
                "for all x (ElectricCar(x) -> HasBattery(x)). [5 * 2 = 10 marks]"
            )
        ],
        [
            sem(
                "i. Tom, Jerry and doctors",
                2,
                "Doctor(Tom) and Patient(Jerry) and for every x, Doctor(x) implies there "
                "exists a y such that Patient(y) and Treats(x, y).",
            ),
            sem(
                "ii. Humans are mortal",
                2,
                "For every x, Human(x) implies Mortal(x), and Human(Socrates), so "
                "Mortal(Socrates) follows.",
            ),
            sem(
                "iii. Students and teachers",
                2,
                "For every s, Student(s) implies there exists a t such that Teacher(t) and "
                "Teaches(t, s); and for every t, Teacher(t) implies Knowledgeable(t).",
            ),
            sem(
                "iv. Cats and mice",
                2,
                "For every x, Cat(x) implies there exists a y such that Mouse(y) and Chases(x, y).",
            ),
            sem(
                "v. Cars, engines and batteries",
                2,
                "For every x, Car(x) implies HasEngine(x), and for every x, ElectricCar(x) "
                "implies HasBattery(x), or the same with engine and battery as objects.",
            ),
        ],
        difficulty=HARD,
        terms=["first-order logic", "predicate", "quantifier", "universal", "existential"],
    ),
    question(
        "K-AI3-Q6",
        "For each of the following sentences, identify the objects, relation, and function "
        "involved. i. The capital of France lies on the River Seine. ii. The sum of the angles "
        "in a triangle equals 180 degrees. iii. The square root of sixteen is four. iv. The "
        "distance between two cities depends on their coordinates. v. The temperature of water "
        "increases with heat. (The key also treats a sixth sentence: The gravitational force "
        "between two bodies decreases with the square of the distance between them.)",
        10,
        LOGIC,
        [
            key(
                "i. Objects: France, Capital(France), River Seine; relation: LiesOn(Capital("
                "France), RiverSeine); function: Capital(x). ii. Objects: Triangle, "
                "Angles(Triangle), 180 degrees; relation: Equals(SumOfAngles(Triangle), 180); "
                "function: SumOfAngles(x). iii. Objects: 16, 4; relation: Equals(Sqrt(16), 4); "
                "function: Sqrt(x). iv. Objects: City1, City2, Coordinates(City), Distance("
                "City1, City2); relation: DependsOn(Distance(C1, C2), Coordinates(C1), "
                "Coordinates(C2)); functions: Coordinates(x), Distance(x, y). v. Objects: "
                "Water, Temperature(Water), Heat; relation: IncreasesWith(Temperature(Water), "
                "Heat); function: Temperature(x). vi. Objects: Body1, Body2, Distance(Body1, "
                "Body2), GravitationalForce(Body1, Body2); relation: DecreasesWith("
                "GravitationalForce(B1, B2), Distance(B1, B2)^2); functions: Distance(x, y), "
                "GravitationalForce(x, y). [5 * 2 = 10 marks]\n"
                "Note: the question lists five sentences plus an unnumbered sixth, and the key "
                "marks 5 * 2; the seed scores the five numbered ones."
            )
        ],
        [
            sem(
                "i. Capital of France",
                2,
                "Objects France and the River Seine, function Capital(x), relation LiesOn.",
            ),
            sem(
                "ii. Angles of a triangle",
                2,
                "Objects Triangle and 180 degrees, function SumOfAngles(x), relation Equals.",
            ),
            sem(
                "iii. Square root of sixteen",
                2,
                "Objects 16 and 4, function Sqrt(x), relation Equals(Sqrt(16), 4).",
            ),
            sem(
                "iv. Distance between cities",
                2,
                "Objects two cities and their coordinates, functions Coordinates and Distance, "
                "relation DependsOn.",
            ),
            sem(
                "v. Temperature of water",
                2,
                "Objects Water and Heat, function Temperature(x), relation IncreasesWith.",
            ),
        ],
        difficulty=MEDIUM,
        terms=["object", "relation", "function", "predicate"],
    ),
    question(
        "K-AI3-Q7",
        "Write the Truth Table Entailment (TT_Entails) algorithm and illustrate it with a "
        "suitable example.",
        10,
        LOGIC,
        [
            key(
                "Semantics of the connectives in a model m: not P is true iff P is false in m; "
                "P and Q is true iff both are true in m; P or Q is true iff either is true in "
                "m; P => Q is true unless P is true and Q is false in m; P <=> Q is true iff P "
                "and Q are both true or both false in m. [2 marks]\n"
                "function TT-ENTAILS?(KB, alpha) returns true or false: symbols <- a list of "
                "the proposition symbols in KB and alpha; return TT-CHECK-ALL(KB, alpha, "
                "symbols, []). function TT-CHECK-ALL(KB, alpha, symbols, model) returns true or "
                "false: if EMPTY?(symbols) then if PL-TRUE?(KB, model) then return PL-TRUE?("
                "alpha, model) else return true; else P <- FIRST(symbols); rest <- REST("
                "symbols); return TT-CHECK-ALL(KB, alpha, rest, EXTEND(P, true, model)) and "
                "TT-CHECK-ALL(KB, alpha, rest, EXTEND(P, false, model)). A suitable example "
                "with its truth table illustrates it. [8 marks]"
            )
        ],
        [
            lst(
                "Semantics of the connectives",
                2,
                [
                    term("not P", "negation"),
                    term("P and Q", "conjunction"),
                    term("P or Q", "disjunction"),
                    term("P implies Q", "implication", "P => Q"),
                    term("P if and only if Q", "biconditional", "P <=> Q"),
                ],
                4,
            ),
            sem(
                "Algorithm: symbols and enumeration of models",
                3,
                "TT-ENTAILS takes the proposition symbols in the knowledge base and the query "
                "and calls TT-CHECK-ALL, which assigns true and false to each symbol in turn "
                "and recurses on the rest of the symbols to enumerate every model.",
            ),
            sem(
                "Algorithm: test of KB and alpha in each model",
                2,
                "When no symbols remain, if the knowledge base is true in the model the "
                "result is whether the query is true in it; if the knowledge base is false the "
                "result is true. The query is entailed only if it holds in every model of KB.",
            ),
            sem(
                "Worked example with a truth table",
                3,
                "A small knowledge base and query are written out as a truth table over all "
                "models, showing the rows where the knowledge base is true and checking the "
                "query in those rows.",
            ),
        ],
        difficulty=HARD,
        terms=["truth table", "entailment", "model", "proposition symbol", "knowledge base"],
    ),
)

QUESTIONS: tuple[QuestionSeed, ...] = (*K_AI1, *K_AI2, *K_AI3)

__all__ = ["QUESTIONS", "SUBJECT"]
