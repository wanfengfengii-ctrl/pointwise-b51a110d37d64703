import math
import unittest
from fractions import Fraction
from analysis_archive import Archive
from analysis_archive.samples import counter, stopped

ABS_TOL = 2e-8
REL_TOL = 2e-8


class RewardSemantics(unittest.TestCase):
    def check_close(self, actual, expected):
        self.assertTrue(math.isclose(actual, expected, abs_tol=ABS_TOL, rel_tol=REL_TOL),
                        (actual, expected))

    def evaluate(self, definition, initial, time):
        with Archive() as archive:
            archive.put_model("m", definition)
            states = [s["id"] for s in definition["states"]]
            archive.put_cohort("c", states, initial, time)
            return archive.run_task(archive.create_task("r", "m", "c"))["statistics"]

    def test_self_event(self):
        result = self.evaluate(counter(), {"running": 1}, 1)
        self.check_close(result["means"][0], 2)
        self.check_close(result["covariance"][0][0], 2)
        self.check_close(result["probabilities"][0], 1)
        empty = counter()
        empty["events"] = []
        other = self.evaluate(empty, {"running": 1}, 1)
        self.assertEqual(other["means"], [0, 0])
        self.assertEqual(other["covariance"], [[0, 0], [0, 0]])

    def test_joint_event(self):
        result = self.evaluate(counter(), {"running": 1}, 1)
        for actual, expected in zip(result["means"], [2, 4]):
            self.check_close(actual, expected)
        for row, expected in zip(result["covariance"], [[2, 4], [4, 8]]):
            for actual, target in zip(row, expected):
                self.check_close(actual, target)

    def test_stopping_integral(self):
        # T=min(J,t), N=1{J<=t}; integrate u*lambda*exp(-lambda*u).
        for time in [0, Fraction(1, 2), 1, 3]:
            t, rate = float(time), 2
            survival = math.exp(-rate * t)
            p = 1 - survival
            et = p / rate
            et2 = 2 * (1 - survival * (1 + rate * t)) / rate ** 2
            etn = (1 - survival * (1 + rate * t)) / rate
            result = self.evaluate(stopped(), {"on": 1}, time)
            for actual, expected in zip(result["probabilities"], [survival, p]):
                self.check_close(actual, expected)
            for actual, expected in zip(result["means"], [et, p]):
                self.check_close(actual, expected)
            expected = [[et2 - et * et, etn - et * p], [etn - et * p, p * (1 - p)]]
            for i in range(2):
                for j in range(2):
                    self.check_close(result["covariance"][i][j], expected[i][j])
        absorbing = self.evaluate(stopped(), {"off": 1}, 2)
        self.assertEqual(absorbing["means"], [0, 0])
        self.assertEqual(absorbing["covariance"], [[0, 0], [0, 0]])

    def test_parallel_signed_and_mixture(self):
        definition = counter(1, (1, 1), ("1/2", -1))
        definition["events"].append({"id": "reverse", "source": "running", "target": "running",
                                      "rate": 2, "reward": [1, -1]})
        result = self.evaluate(definition, {"running": 1}, 1)
        for actual, expected in zip(result["means"], [3.5, -2]):
            self.check_close(actual, expected)
        for i in range(2):
            for j in range(2):
                self.check_close(result["covariance"][i][j], [[3, -1], [-1, 3]][i][j])
        definition = {"metrics": ["count", "signed"],
                      "states": [{"id": "a", "reward": [0, 0]}, {"id": "b", "reward": [0, 0]}],
                      "events": [{"id": "one", "source": "a", "target": "b", "rate": 1, "reward": [1, 1]},
                                 {"id": "two", "source": "a", "target": "b", "rate": 2, "reward": [1, -1]}]}
        result = self.evaluate(definition, {"a": "3/4", "b": "1/4"}, 1)
        p = 0.75 * (1 - math.exp(-3))
        means = [p, -p / 3]
        raw = [[p, -p / 3], [-p / 3, p]]
        for i in range(2):
            self.check_close(result["means"][i], means[i])
            for j in range(2):
                self.check_close(result["covariance"][i][j], raw[i][j] - means[i] * means[j])
        self.check_close(result["probabilities"][0], 0.75 * math.exp(-3))
        self.check_close(result["probabilities"][1], 1 - 0.75 * math.exp(-3))

    def test_supported_limits(self):
        g, r = [1, -1, 2], [2, 3, -1]
        states = [{"id": str(i), "reward": r} for i in range(6)]
        events = [{"id": str(3 * i + j), "source": str(i), "target": str((i + j) % 6),
                   "rate": [2, 3, 3][j], "reward": g} for i in range(6) for j in range(3)]
        result = self.evaluate({"metrics": ["a", "b", "c"], "states": states, "events": events},
                               {str(i): "1/6" for i in range(6)}, 8)
        for p in result["probabilities"]:
            self.check_close(p, 1 / 6)
        for i in range(3):
            self.check_close(result["means"][i], 64 * g[i] + 8 * r[i])
            for j in range(3):
                self.check_close(result["covariance"][i][j], 64 * g[i] * g[j])
