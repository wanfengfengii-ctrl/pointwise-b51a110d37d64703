import math
import unittest
from fractions import Fraction
from analysis_archive import Archive, ValidationError, NotFound, ResultNotReady
from analysis_archive.samples import counter, stopped


class BusinessFlow(unittest.TestCase):
    def setUp(self):
        self.archive = Archive()

    def tearDown(self):
        self.archive.close()

    def test_deterministic(self):
        a = self.archive
        definition = {"metrics": ["uptime"], "states": [{"id": "idle", "reward": [3]}], "events": []}
        self.assertEqual(a.put_model("machine", definition), 1)
        a.put_cohort("shift", ["idle"], {"idle": 1}, 2)
        task = a.create_task("run", "machine", "shift")
        with self.assertRaises(ResultNotReady):
            a.get_result(task)
        result = a.run_task(task)
        self.assertEqual(result["statistics"]["means"], [6])
        self.assertEqual(result["statistics"]["covariance"], [[0]])
        self.assertEqual(result["statistics"]["probabilities"], [1])
        self.assertEqual(result, a.get_result(task))
        self.assertEqual(result, a.run_task(task))
        self.assertEqual(len(result["receipt"]), 64)
        self.assertEqual(a.get_task(task)["status"], "succeeded")

    def test_invalid_cohort(self):
        a = self.archive
        a.put_model("m", counter())
        a.put_cohort("c", ["running"], {"running": 1}, 1)
        before = a.get_cohort("c")
        for initial, time in [({"running": "1/2"}, 1), ({"missing": 1}, 1),
                              ({"running": -1}, 1), ({"running": 1}, -1),
                              ({"running": 1}, math.nan), ({"running": 1}, math.inf)]:
            with self.subTest(initial=initial, time=time):
                with self.assertRaises(ValidationError):
                    a.put_cohort("c", ["running"], initial, time)
                self.assertEqual(a.get_cohort("c"), before)
        with self.assertRaises(NotFound):
            a.create_task("missing", "m", "absent")
        a.put_cohort("different", ["idle"], {"idle": 1}, 1)
        with self.assertRaises(ValidationError):
            a.create_task("different", "m", "different")
        self.assertEqual(a.store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_snapshot_and_copy_isolation(self):
        a = self.archive
        definition = counter()
        a.put_model("m", definition)
        definition["events"][0]["rate"] = 7
        a.put_cohort("c", ["running"], {"running": Fraction(1)}, 1)
        task = a.create_task("r", "m", "c")
        a.put_cohort("c", ["running"], {"running": 1}, 2)
        result = a.run_task(task)
        self.assertAlmostEqual(result["statistics"]["means"][0], 2)
        result["statistics"]["means"][0] = 999
        self.assertAlmostEqual(a.get_result(task)["statistics"]["means"][0], 2)
        exported = a.get_model("m")
        exported["definition"]["events"].clear()
        self.assertEqual(len(a.get_model("m")["definition"]["events"]), 1)
        self.assertEqual(a.get_task(task)["parameters"]["duration"], 1)

    def test_errors_and_zero_time(self):
        a = self.archive
        for call in [lambda: a.get_model("none"), lambda: a.get_cohort("none"),
                     lambda: a.get_task(1), lambda: a.get_result(1), lambda: a.run_task(1)]:
            with self.assertRaises(NotFound):
                call()
        a.put_model("设备", counter())
        a.put_cohort("零", ["running"], {"running": 1}, 0)
        result = a.run_task(a.create_task("请求", "设备", "零"))["statistics"]
        self.assertEqual(result["means"], [0, 0])
        self.assertEqual(result["covariance"], [[0, 0], [0, 0]])
        self.assertEqual(result["probabilities"], [1])

    def test_state_probability_api(self):
        a = self.archive
        a.put_model("m", stopped())
        self.assertEqual(a.state_probabilities("m", {"on": 1}, 0), {"on": 1, "off": 0})
        original = a.state_probabilities("m", {"on": 1}, 1)
        self.assertAlmostEqual(original["on"], math.exp(-2))
        definition = stopped()
        definition["events"][0]["rate"] = 3
        a.put_model("m", definition)
        self.assertAlmostEqual(a.state_probabilities("m", {"on": 1}, 1)["on"], math.exp(-3))
        self.assertEqual(a.state_probabilities("m", {"on": 1}, 1, revision=1), original)
        with self.assertRaises(ValidationError):
            a.state_probabilities("m", {"unknown": 1}, 1)
