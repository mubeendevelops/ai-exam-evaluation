"""Optimal assignment (the Hungarian method, Kuhn-Munkres with potentials), O(n³) in the
standard library: diagrams have tens of nodes, so no numeric library is needed."""

from collections.abc import Sequence

INF = float("inf")


def assign(cost: Sequence[Sequence[float]]) -> list[int]:
    """For a square cost matrix, the column given to each row so that the total cost is the
    least possible. Entries may be ``inf`` (forbidden) as long as some finite assignment
    exists."""
    n = len(cost)
    if n == 0:
        return []
    if any(len(row) != n for row in cost):
        raise ValueError("the cost matrix must be square")
    # 1-based arrays; u, v potentials; p[j] = row matched to column j; way = augmenting path.
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, n + 1):
                if used[j]:
                    continue
                c = cost[i0 - 1][j - 1]
                cur = INF if c == INF else c - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if j1 < 0 or delta == INF:
                raise ValueError("no finite assignment exists")
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [0] * n
    for j in range(1, n + 1):
        result[p[j] - 1] = j - 1
    return result
