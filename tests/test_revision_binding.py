import unittest
from analysis_archive import Archive, ValidationError, NotFound
from analysis_archive.samples import counter


class RevisionBinding(unittest.TestCase):
    def setUp(self):
        self.archive = Archive()
        self.archive.put_model("m", counter())
        self.archive.put_cohort("c", ["running"], {"running": 1}, 1)

    def tearDown(self):
        self.archive.close()

    def test_fixed_revision(self):
        a = self.archive
        old = a.create_task("old", "m", "c")
        a.put_model("m", counter(3))
        new = a.create_task("new", "m", "c")
        explicit = a.create_task("explicit", "m", "c", revision=1)
        self.assertEqual(a.revisions("m"), [1, 2])
        self.assertEqual(a.get_model("m")["revision"], 2)
        for task, revision, mean in [(old, 1, 2), (new, 2, 3), (explicit, 1, 2)]:
            result = a.run_task(task)
            self.assertEqual(result["revision"], revision)
            self.assertAlmostEqual(result["statistics"]["means"][0], mean)
        self.assertEqual(a.create_task("old", "m", "c"), old)
        with self.assertRaises(NotFound):
            a.create_task("absent", "m", "c", revision=3)

    def test_invalid_model(self):
        a = self.archive
        task = a.create_task("old", "m", "c")
        bad = []
        for key, value in [("rate", -1), ("target", "missing"), ("rate", 9),
                           ("reward", [1]), ("id", "")]:
            definition = counter()
            definition["events"][0][key] = value
            bad.append(definition)
        duplicate_event = counter()
        duplicate_event["events"] *= 2
        bad.append(duplicate_event)
        duplicate_state = counter()
        duplicate_state["states"] *= 2
        bad.append(duplicate_state)
        duplicate_metric = counter()
        duplicate_metric["metrics"] = ["x", "x"]
        bad.append(duplicate_metric)
        for definition in bad:
            with self.subTest(definition=definition):
                with self.assertRaises(ValidationError):
                    a.put_model("m", definition)
                self.assertEqual(a.revisions("m"), [1])
                self.assertEqual(a.get_model("m")["revision"], 1)
        self.assertAlmostEqual(a.run_task(task)["statistics"]["means"][0], 2)
