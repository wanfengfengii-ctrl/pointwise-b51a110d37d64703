import math
import unittest
from decimal import Decimal, localcontext
from vendor.sdk2 import propagate, VERSION


def decimal_action(matrix, vector, time):
    # Independent 70-digit vector Taylor series, without scaling/squaring.
    with localcontext() as context:
        context.prec = 70
        a = [[Decimal(str(x)) for x in row] for row in matrix]
        term = [Decimal(str(x)) for x in vector]
        answer = term[:]
        t = Decimal(str(time))
        for order in range(1, 240):
            term = [sum(a[i][j] * term[j] for j in range(len(term))) * t / order
                    for i in range(len(term))]
            answer = [x + y for x, y in zip(answer, term)]
            if max(abs(x) for x in term) < Decimal("1e-60"):
                return [float(x) for x in answer]
        raise AssertionError("independent reference failed to converge")


class MatrixPort(unittest.TestCase):
    def close_vector(self, actual, expected):
        for a, b in zip(actual, expected):
            self.assertTrue(math.isclose(a, b, abs_tol=2e-10, rel_tol=2e-10), (a, b))

    def test_column_and_nondiagonalizable(self):
        self.assertEqual(VERSION, "2.0")
        self.close_vector(propagate([[0, 1, 0], [0, 0, 1], [0, 0, 0]], [0, 0, 1], 2), [2, 2, 1])
        self.close_vector(propagate([[-2, 0], [2, 0]], [1, 0], 1),
                          [math.exp(-2), 1 - math.exp(-2)])
        self.close_vector(propagate([[0, -1], [1, 0]], [1, 0], 1),
                          [math.cos(1), math.sin(1)])

    def test_independent_decimal(self):
        matrices = [
            [[-2, 1, 0], [3, -0.5, 0], [0, 2, 0]],
            [[0.2, -0.3, 0.1], [0.7, -1.2, 0.4], [0, 0.6, -0.8]],
        ]
        for a in matrices:
            x = [0.3, -0.2, 1]
            self.close_vector(propagate(a, x, 1.5), decimal_action(a, x, 1.5))

    def test_semigroup_and_large_negative(self):
        a = [[-3, 2, 0], [1, -4, 2], [2, 2, -2]]
        x = [0.2, 0.3, 0.5]
        self.close_vector(propagate(a, x, 2),
                          propagate(a, propagate(a, x, 0.75), 1.25))
        self.close_vector(propagate([[-100]], [1], 1), [math.exp(-100)])
        a = [[float(i == j) for j in range(60)] for i in range(60)]
        self.close_vector(propagate(a, [1] * 60, 0.5), [math.exp(0.5)] * 60)
        self.assertEqual(propagate([[0]], [3], 8), [3])
        self.assertEqual(propagate([[1]], [3], 0), [3])

    def test_rejections_and_input_ownership(self):
        matrix, vector = [[0, 1], [0, 0]], [0, 1]
        self.assertEqual(propagate(matrix, vector, 1), [1, 1])
        self.assertEqual(matrix, [[0, 1], [0, 0]])
        self.assertEqual(vector, [0, 1])
        for a, x, t in [([], [], 1), ([[1, 2]], [1], 1), ([[1]], [1], -1),
                        ([[math.nan]], [1], 1), ([[1]], [math.inf], 1),
                        ([[1]], [1], math.nan), ([[65537]], [1], 1)]:
            with self.subTest(a=a, x=x, t=t), self.assertRaises(ValueError):
                propagate(a, x, t)
