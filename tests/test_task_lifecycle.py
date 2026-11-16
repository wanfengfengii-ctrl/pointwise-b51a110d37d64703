import tempfile
import unittest
from pathlib import Path
from analysis_archive import Archive, RequestConflict, ResultNotReady
from analysis_archive.samples import counter, build_legacy


class InjectedFailure(RuntimeError):
    pass


def fail_at(target):
    def fault(stage):
        if stage == target:
            raise InjectedFailure(stage)
    return fault


class TaskLifecycle(unittest.TestCase):
    def test_replay_and_conflict(self):
        with Archive() as a:
            a.put_model("m", counter())
            a.put_cohort("c", ["running"], {"running": 1}, 1)
            task = a.create_task("request", "m", "c")
            result = a.run_task(task)
            a.put_model("m", counter(3))
            a.put_cohort("c", ["running"], {"running": 1}, 2)
            self.assertEqual(a.create_task("request", "m", "c"), task)
            for arguments in [("m", "c", 1), ("m", "other", None), ("different", "c", None)]:
                with self.assertRaises(RequestConflict):
                    a.create_task("request", arguments[0], arguments[1], arguments[2])
            self.assertEqual(a.run_task(task), result)
            self.assertEqual(a.store.connection.execute("SELECT COUNT(*) FROM results").fetchone()[0], 1)

    def test_rollback_in_memory(self):
        for stage in ["after_compute", "after_result_insert", "before_commit"]:
            with self.subTest(stage=stage), Archive() as a:
                a.put_model("m", counter())
                a.put_cohort("c", ["running"], {"running": 1}, 1)
                task = a.create_task("r", "m", "c")
                with self.assertRaises(InjectedFailure):
                    a.run_task(task, fault=fail_at(stage))
                self.assertEqual(a.get_task(task)["status"], "pending")
                self.assertEqual(a.store.connection.execute("SELECT COUNT(*) FROM results").fetchone()[0], 0)
                with self.assertRaises(ResultNotReady):
                    a.get_result(task)
                self.assertAlmostEqual(a.run_task(task)["statistics"]["means"][0], 2)

    def test_rollback_reopen(self):
        for stage in ["after_compute", "after_result_insert", "before_commit"]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "archive.sqlite"
                with Archive(path) as a:
                    a.put_model("m", counter())
                    a.put_cohort("c", ["running"], {"running": 1}, 1)
                    task = a.create_task("request", "m", "c")
                    with self.assertRaises(InjectedFailure):
                        a.run_task(task, fault=fail_at(stage))
                with Archive(path) as a:
                    self.assertEqual(a.get_task(task)["status"], "pending")
                    self.assertEqual(a.store.connection.execute("SELECT COUNT(*) FROM results").fetchone()[0], 0)
                    with self.assertRaises(ResultNotReady):
                        a.get_result(task)
                    a.put_model("m", counter(7))
                    a.put_cohort("c", ["running"], {"running": 1}, 2)
                    self.assertEqual(a.create_task("request", "m", "c"), task)
                    result = a.run_task(task)
                    self.assertAlmostEqual(result["statistics"]["means"][0], 2)
                with Archive(path) as a:
                    self.assertEqual(a.get_result(task), result)
                    self.assertEqual(a.run_task(task), result)
                    self.assertEqual(a.store.connection.execute("SELECT COUNT(*) FROM results").fetchone()[0], 1)

    def test_complete_legacy_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite"
            ids = build_legacy(path)
            with Archive(path) as a:
                self.assertEqual(a.store.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(a.store.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(a.revisions("press"), [1, 2])
                self.assertEqual(a.get_task(ids["pending"])["status"], "pending")
                self.assertEqual(a.get_task(ids["completed"])["revision"], 1)
                self.assertEqual(a.get_task(ids["revised"])["revision"], 2)
                old = a.get_result(ids["completed"])
                self.assertAlmostEqual(old["statistics"]["means"][0], 2)
                self.assertAlmostEqual(a.run_task(ids["pending"])["statistics"]["means"][0], 2)
                self.assertAlmostEqual(a.get_result(ids["revised"])["statistics"]["means"][0], 6)
                self.assertEqual(a.get_result(ids["completed"]), old)
            with Archive(path) as a:
                self.assertEqual(a.get_result(ids["completed"]), old)
                self.assertEqual(len({a.get_result(t)["receipt"] for t in ids.values()}), 5)
