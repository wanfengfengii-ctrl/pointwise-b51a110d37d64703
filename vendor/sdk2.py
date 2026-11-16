"""Matrix SDK 2. Column convention: propagate(A, x, t) = exp(t*A) @ x.

Finite real matrices, 1..96 dimensions; t>=0; t*||A||_infinity<=65536.
No Markov, event, reward, persistence or archive conversion interfaces.
"""
import math

VERSION = "2.0"


def _multiply(a, b):
    n = len(a)
    columns = list(zip(*b))
    return [[math.fsum(a[i][k] * columns[j][k] for k in range(n))
             for j in range(n)] for i in range(n)]


def propagate(matrix, vector, time):
    try:
        a = [[float(x) for x in row] for row in matrix]
        x = [float(v) for v in vector]
        t = float(time)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("expected finite real inputs") from exc
    n = len(x)
    if not 1 <= n <= 96 or len(a) != n or any(len(row) != n for row in a):
        raise ValueError("matrix shape or dimension")
    if not math.isfinite(t) or t < 0 or any(not math.isfinite(v) for row in a for v in row) or any(not math.isfinite(v) for v in x):
        raise ValueError("expected finite inputs and nonnegative time")
    if t == 0:
        return x[:]
    try:
        norm = max(math.fsum(abs(v) for v in row) for row in a)
    except OverflowError as exc:
        raise ValueError("matrix norm overflow") from exc
    size = norm * t
    if not math.isfinite(size) or size > 65536:
        raise ValueError("scaled matrix norm exceeds 65536")
    if t == 0 or size == 0:
        return x[:]
    squarings = max(0, math.ceil(math.log2(size / 0.5)))
    factor = t / (2 ** squarings)
    b = [[v * factor for v in row] for row in a]
    result = [[float(i == j) for j in range(n)] for i in range(n)]
    term = [row[:] for row in result]
    for order in range(1, 65):
        product = _multiply(term, b)
        term = [[v / order for v in row] for row in product]
        result = [[result[i][j] + term[i][j] for j in range(n)] for i in range(n)]
        if max(math.fsum(abs(v) for v in row) for row in term) < 2e-17:
            break
    else:
        raise ArithmeticError("matrix series did not converge")
    for _ in range(squarings):
        result = _multiply(result, result)
        if any(not math.isfinite(v) for row in result for v in row):
            raise ArithmeticError("nonfinite matrix exponential")
    answer = [math.fsum(result[i][j] * x[j] for j in range(n)) for i in range(n)]
    if any(not math.isfinite(v) for v in answer):
        raise ArithmeticError("nonfinite propagated vector")
    return answer
