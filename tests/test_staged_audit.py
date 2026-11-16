"""Independent audit for the fixed two-stage joint analysis.

The checks here never compare the new implementation against itself:

* analytic expectations are derived by hand from exponential/Poisson formulas;
* stage-one moments are cross-checked against the public vendor.sdk1.analyze;
* split plans are reconciled against the original single-stage SDK1 report;
* the public staged_examples fixture supplies its own independently derived
  expectations for random initial states and reordered directories.
"""
import copy
import math
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from analysis_archive import (
    Archive, NotFound, RequestConflict, ResultNotReady, ValidationError)
from analysis_archive.samples import build_legacy, counter, parallel
from analysis_archive.staged_engine import stage_moments
from vendor.sdk1 import analyze
import staged_examples

TOL = 2e-8


def close(actual, expected):
    return abs(actual - expected) <= TOL + TOL * abs(expected)


def jump_model(rate=1, event_reward=(1, 0), state_rewards=None):
    state_rewards = state_rewards or ((0, 0), (0, 0))
    return {"metrics": ["m", "n"],
            "states": [{"id": "on", "reward": list(state_rewards[0])},
                       {"id": "off", "reward": list(state_rewards[1])}],
            "events": [{"id": "jump", "source": "on", "target": "off",
                        "rate": rate, "reward": list(event_reward)}]}


def residence_model(rewards):
    # Directory deliberately reversed relative to jump_model; metric order too.
    return {"metrics": ["n", "m"],
            "states": [{"id": "off", "reward": list(rewards["off"])},
                       {"id": "on", "reward": list(rewards["on"])}],
            "events": []}


class StagedAudit(unittest.TestCase):
    def assertMatrixClose(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for row, target in zip(actual, expected):
            self.assertEqual(len(row), len(target))
            for a, b in zip(row, target):
                self.assertTrue(type(a) is float and math.isfinite(a), a)
                self.assertTrue(close(a, b), (a, b))

    def assertVectorClose(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected):
            self.assertTrue(type(a) is float and math.isfinite(a), a)
            self.assertTrue(close(a, b), (a, b))

    # ------------------------------------------------------------------
    # 1. Public fixture: independently derived analytic expectations.
    # ------------------------------------------------------------------
    def test_public_examples_check_and_demo(self):
        reports = staged_examples.check()
        self.assertEqual(len(reports), 3)
        for report in reports:
            self.assertEqual(
                set(report), {"task_id", "request_id", "stages", "parameters",
                              "statistics", "receipt"})
            stats = report["statistics"]
            self.assertEqual(stats["coordinates"],
                             [[1, "count"], [1, "signed"], [2, "count"], [2, "signed"]])
            # covariance must be symmetric
            n = len(stats["covariance"])
            for i in range(n):
                for j in range(n):
                    self.assertTrue(close(stats["covariance"][i][j],
                                          stats["covariance"][j][i]))
        demo = staged_examples.demo()
        self.assertTrue(demo["rollback_then_reopen"])

    # ------------------------------------------------------------------
    # 2. Hand-derived cross-stage dependence (STAGED.md single metric case).
    # ------------------------------------------------------------------
    def test_hand_case_cross_stage_dependence(self):
        first = {"metrics": ["m"],
                 "states": [{"id": "on", "reward": [0]}, {"id": "off", "reward": [0]}],
                 "events": [{"id": "jump", "source": "on", "target": "off",
                             "rate": 1, "reward": [1]}]}
        second = {"metrics": ["m"],
                  "states": [{"id": "on", "reward": [0]}, {"id": "off", "reward": [1]}],
                  "events": []}
        with Archive() as archive:
            archive.put_model("operating", first)
            archive.put_model("maintenance", second)
            task = archive.create_staged_task(
                "hand", [("operating", None, 1), ("maintenance", None, 1)], {"on": 1})
            stats = archive.run_staged_task(task)["statistics"]
            p = 1 - math.exp(-1)
            self.assertVectorClose(stats["means"], [p, p])
            # Every block equals p(1-p): the two cumulative amounts are equal
            # pathwise here, so independence would wrongly give zero cross block.
            self.assertMatrixClose(stats["covariance"],
                                   [[p * (1 - p), p * (1 - p)],
                                    [p * (1 - p), p * (1 - p)]])
            self.assertVectorClose(stats["total_means"], [2 * p])
            self.assertMatrixClose(stats["total_covariance"], [[4 * p * (1 - p)]])
            self.assertVectorClose(stats["probabilities"], [math.exp(-1), p])

    def test_cross_covariance_analytic_with_residence(self):
        # Stage 1: on -> off at rate 1, event pays m=1, no residence reward.
        # Stage 2: off accumulates m at rate 2, on accumulates nothing.
        first = jump_model(1, (1, 0))
        second = residence_model({"off": (0, 2), "on": (0, 0)})
        with Archive() as archive:
            archive.put_model("op", first)
            archive.put_model("mt", second)
            task = archive.create_staged_task(
                "an", [("op", None, 1), ("mt", None, 1)], {"on": 1})
            stats = archive.run_staged_task(task)["statistics"]
            p = 1 - math.exp(-1)
            # Y1 = Bernoulli(p); Y2 = 2 * 1{terminal off}; same indicator.
            self.assertVectorClose(stats["means"], [p, 0, 2 * p, 0])
            self.assertMatrixClose(
                stats["covariance"],
                [[p * (1 - p), 0, 2 * p * (1 - p), 0],
                 [0, 0, 0, 0],
                 [2 * p * (1 - p), 0, 4 * p * (1 - p), 0],
                 [0, 0, 0, 0]])
            self.assertMatrixClose(stats["total_covariance"],
                                   [[9 * p * (1 - p), 0], [0, 0]])

    # ------------------------------------------------------------------
    # 3. Reordered second-stage directory and pinned revision after correction.
    # ------------------------------------------------------------------
    def test_directory_reorder_and_revision_correction(self):
        first = jump_model(1, (1, -2), ((0, 0), (0, 0)))
        # Second model reverses both directories; residence accrues only in off,
        # so its conditional contribution is the rate times the terminal mass.
        second = residence_model({"off": (3, 4), "on": (0, 0)})
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "a.sqlite")
            with Archive(path) as archive:
                archive.put_model("op", first)
                archive.put_model("mt", second)
                task = archive.create_staged_task(
                    "fixed", [("op", None, 1), ("mt", None, 1)], {"on": 1})
                snapshot = archive.get_staged_task(task)
                # Correct both models with reversed directories and new rates.
                corrected_first = copy.deepcopy(first)
                corrected_first["states"].reverse()
                corrected_first["events"][0]["rate"] = 4
                corrected_second = copy.deepcopy(second)
                corrected_second["states"].reverse()
                archive.put_model("op", corrected_first)
                archive.put_model("mt", corrected_second)
                # Replay with normalized args returns the same frozen task.
                same = archive.create_staged_task(
                    "fixed", (("op", None, 1.0), ("mt", None, 1.0)), {"on": "1", "off": "0"})
                self.assertEqual(same, task)
                self.assertEqual(archive.get_staged_task(task), snapshot)
                report = archive.run_staged_task(task)
            with Archive(path) as archive:
                reopened = archive.run_staged_task(task)
                self.assertEqual(reopened, report)
                self.assertEqual([s["revision"] for s in reopened["stages"]], [1, 1])
                stats = reopened["statistics"]
                p = 1 - math.exp(-1)
                # Stage one metrics m,n in first-model order.
                self.assertVectorClose(stats["means"][:2], [p, -2 * p])
                # Stage two: off gets rate m=4, n=3 for one unit when terminal
                # is off; joined by ID despite the reversed second directory.
                self.assertVectorClose(stats["means"][2:], [4 * p, 3 * p])
                self.assertEqual(stats["states"], ["on", "off"])
                self.assertEqual(stats["metrics"], ["m", "n"])
                # A new request resolves the current (second) revisions.
                newer = archive.create_staged_task(
                    "new", [("op", None, 1), ("mt", None, 1)], {"on": 1})
                self.assertEqual([s["revision"] for s in
                                  archive.get_staged_task(newer)["stages"]], [2, 2])

    # ------------------------------------------------------------------
    # 4. Random initial state with hand-derived analytic expectations.
    # ------------------------------------------------------------------
    def test_random_initial_analytic(self):
        first = jump_model(1, (1, 0))
        second = residence_model({"off": (0, 1), "on": (0, 0)})
        q = Fraction(1, 3)
        with Archive() as archive:
            archive.put_model("op", first)
            archive.put_model("mt", second)
            task = archive.create_staged_task(
                "mix", [("op", None, 1), ("mt", None, 1)],
                {"on": "1/3", "off": "2/3"})
            stats = archive.run_staged_task(task)["statistics"]
            survival = math.exp(-1)
            a = float(q) * (1 - survival)          # E[Y1 m]
            b = 1 - float(q) * survival            # P(terminal off) = E[Y2 m]
            self.assertVectorClose(stats["means"], [a, 0, b, 0])
            self.assertTrue(close(stats["probabilities"][0], float(q) * survival))
            self.assertTrue(close(stats["probabilities"][1], b))
            var_a, var_b = a * (1 - a), b * (1 - b)
            # E[Y1 Y2] = a (Y1>0 implies terminal off), so Cov = a - a*b.
            cross = a - a * b
            self.assertMatrixClose(
                stats["covariance"],
                [[var_a, 0, cross, 0], [0, 0, 0, 0],
                 [cross, 0, var_b, 0], [0, 0, 0, 0]])
            self.assertVectorClose(stats["total_means"], [a + b, 0])
            self.assertMatrixClose(stats["total_covariance"],
                                   [[var_a + var_b + 2 * cross, 0], [0, 0]])

    # ------------------------------------------------------------------
    # 5. Zero-duration semantics on either side and both sides.
    # ------------------------------------------------------------------
    def test_zero_duration_variants(self):
        first = jump_model(1, (1, -1))
        second = residence_model({"off": (2, 3), "on": (0, 0)})
        with Archive() as archive:
            archive.put_model("op", first)
            archive.put_model("mt", second)
            # Zero first stage: state unchanged, first block fully zero.
            t01 = archive.create_staged_task("z01",
                [("op", None, 0), ("mt", None, 1)], {"on": 1})
            s01 = archive.run_staged_task(t01)["statistics"]
            self.assertVectorClose(s01["means"][:2], [0, 0])
            self.assertMatrixClose([row[:2] for row in s01["covariance"][:2]],
                                   [[0, 0], [0, 0]])
            self.assertMatrixClose([row[2:] for row in s01["covariance"][:2]],
                                   [[0, 0], [0, 0]])
            self.assertVectorClose(s01["probabilities"], [1, 0])
            self.assertVectorClose(s01["means"][2:], [0, 0])
            self.assertMatrixClose(s01["total_covariance"], [[0, 0], [0, 0]])
            # Zero second stage: second block and cross blocks fully zero.
            t10 = archive.create_staged_task("z10",
                [("op", None, 1), ("mt", None, 0)], {"on": 1})
            s10 = archive.run_staged_task(t10)["statistics"]
            p = 1 - math.exp(-1)
            self.assertVectorClose(s10["means"][2:], [0, 0])
            self.assertMatrixClose([row[2:] for row in s10["covariance"][2:]],
                                   [[0, 0], [0, 0]])
            self.assertMatrixClose([row[2:] for row in s10["covariance"][:2]],
                                   [[0, 0], [0, 0]])
            self.assertVectorClose(s10["means"][:2], [p, -p])
            self.assertVectorClose(s10["probabilities"], [math.exp(-1), p])
            # Both zero: everything zero and initial state is preserved.
            t00 = archive.create_staged_task("z00",
                [("op", None, 0), ("mt", None, 0)], {"on": "1/4", "off": "3/4"})
            s00 = archive.run_staged_task(t00)["statistics"]
            self.assertVectorClose(s00["means"], [0, 0, 0, 0])
            self.assertMatrixClose(s00["covariance"],
                                   [[0.0] * 4 for _ in range(4)])
            self.assertMatrixClose(s00["total_covariance"], [[0, 0], [0, 0]])
            self.assertVectorClose(s00["probabilities"], [0.25, 0.75])

    # ------------------------------------------------------------------
    # 6. Self events and shared multi-metric payment per occurrence.
    # ------------------------------------------------------------------
    def test_self_event_shared_vector_payment(self):
        model = counter(2, (1, 2))  # one self event, two metrics, rate 2, t=1
        with Archive() as archive:
            archive.put_model("m", model)
            task = archive.create_staged_task(
                "self", [("m", None, 1), ("m", None, 1)], {"running": 1})
            stats = archive.run_staged_task(task)["statistics"]
            # Poisson(2) per stage: mean 2, variance 2; the perfectly shared
            # vector gives within-stage Cov(metric0, metric1) = 4. The single
            # state is absorbing, so the boundary carries no information and the
            # independent stages have zero cross blocks.
            self.assertVectorClose(stats["means"], [2, 4, 2, 4])
            self.assertMatrixClose(
                stats["covariance"],
                [[2, 4, 0, 0], [4, 8, 0, 0],
                 [0, 0, 2, 4], [0, 0, 4, 8]])
            self.assertVectorClose(stats["total_means"], [4, 8])
            self.assertMatrixClose(stats["total_covariance"],
                                   [[4, 8], [8, 16]])
            self.assertVectorClose(stats["probabilities"], [1])

    def test_self_event_cross_stage_with_random_boundary(self):
        # A self event on one of two states keeps a random terminal boundary,
        # so the residence-only second stage must show a nonzero cross block.
        model = {"metrics": ["m"],
                 "states": [{"id": "a", "reward": [0]}, {"id": "b", "reward": [0]}],
                 "events": [{"id": "beat", "source": "b", "target": "b",
                             "rate": 2, "reward": [1]}]}
        residence = {"metrics": ["m"],
                     "states": [{"id": "a", "reward": [3]}, {"id": "b", "reward": [0]}],
                     "events": []}
        with Archive() as archive:
            archive.put_model("op", model)
            archive.put_model("mt", residence)
            task = archive.create_staged_task(
                "beat", [("op", None, 1), ("mt", None, 1)],
                {"a": "1/2", "b": "1/2"})
            stats = archive.run_staged_task(task)["statistics"]
            # Y1 is a mixture: zero on paths starting at a (probability 1/2) and
            # Poisson(2) on the b half. E[Y1]=1, E[Y1^2]=3, so Var(Y1)=2.
            self.assertTrue(close(stats["means"][0], 1.0))
            self.assertTrue(close(stats["covariance"][0][0], 2.0))
            # Y2 = 3 * 1{terminal a}; Y1 Y2 = 0 pathwise (beats occur only at b,
            # residence only accrues at a), hence Cov = -E[Y1] E[Y2] < 0.
            cross = stats["covariance"][0][1]
            self.assertTrue(close(cross, -stats["means"][0] * stats["means"][1]))
            self.assertLess(cross, 0.0)

    # ------------------------------------------------------------------
    # 7. Parallel events and negative payments.
    # ------------------------------------------------------------------
    def test_parallel_negative_payments(self):
        model = parallel()
        initial = {"a": "3/4", "b": "1/4"}
        with Archive() as archive:
            archive.put_model("m", model)
            task = archive.create_staged_task(
                "par", [("m", None, 1), ("m", None, 1)], initial)
            stats = archive.run_staged_task(task)["statistics"]
            reference = analyze(model, initial, 1)
            # Stage one starts from the stored initial distribution and
            # reproduces the independent SDK1 single-stage report exactly.
            k = 2
            for i in range(k):
                self.assertTrue(close(stats["means"][i], reference["means"][i]))
                for j in range(k):
                    self.assertTrue(close(stats["covariance"][i][j],
                                          reference["covariance"][i][j]))
            # Stage two starts from the terminal distribution, so its means are
            # the terminal-mass-weighted SDK1 conditional means, joined by ID.
            terminal = reference["probabilities"]
            state_ids = [s["id"] for s in model["states"]]
            expected_stage_two = [0.0] * k
            for a, state_id in enumerate(state_ids):
                point = {s: (1 if s == state_id else 0) for s in state_ids}
                conditional = analyze(model, point, 1)
                for i in range(k):
                    expected_stage_two[i] += terminal[a] * conditional["means"][i]
            for i in range(k):
                self.assertTrue(close(stats["means"][k + i], expected_stage_two[i]))
            for i in range(k):
                for j in range(k):
                    self.assertTrue(math.isfinite(stats["covariance"][k + i][k + j]))
            # Total means add the two stage means (the second stage starts from
            # the terminal distribution, not the initial one).
            for i in range(k):
                self.assertTrue(close(stats["total_means"][i],
                                      stats["means"][i] + stats["means"][k + i]))
            # Total covariance must include both cross blocks: recompute it from
            # the four reported blocks as an independent identity, rather than
            # adding only the two within-stage diagonals.
            for i in range(k):
                for j in range(k):
                    expected = (stats["covariance"][i][j]
                                + stats["covariance"][k + i][k + j]
                                + stats["covariance"][i][k + j]
                                + stats["covariance"][k + i][j])
                    self.assertTrue(close(stats["total_covariance"][i][j], expected))
            # The interacting chain with a random terminal state yields a
            # nonzero cross block (independence would incorrectly zero it).
            self.assertTrue(any(abs(stats["covariance"][i][k + j]) > 1e-6
                                for i in range(k) for j in range(k)))

    # ------------------------------------------------------------------
    # 8. All three fault points, rollback and reopen recovery.
    # ------------------------------------------------------------------
    def test_faults_rollback_and_reopen(self):
        class Boom(RuntimeError):
            pass
        for stage_name in ["after_compute", "after_result_insert", "before_commit"]:
            with self.subTest(stage=stage_name), tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "a.sqlite")
                with Archive(path) as archive:
                    archive.put_model("m", counter())
                    task = archive.create_staged_task(
                        "r", [("m", None, 1), ("m", None, 1)], {"running": 1})

                    def fault(stage, target=stage_name):
                        if stage == target:
                            raise Boom(stage)
                    with self.assertRaises(Boom):
                        archive.run_staged_task(task, fault=fault)
                    self.assertEqual(archive.get_staged_task(task)["status"], "pending")
                    self.assertEqual(
                        archive.store.connection.execute(
                            "SELECT COUNT(*) FROM staged_results").fetchone()[0], 0)
                    with self.assertRaises(ResultNotReady):
                        archive.get_staged_result(task)
                with Archive(path) as archive:
                    report = archive.run_staged_task(task)
                    self.assertEqual(archive.run_staged_task(task), report)
                    self.assertEqual(archive.get_staged_result(task), report)
                    self.assertEqual(archive.get_staged_task(task)["status"], "succeeded")

    def test_successful_rerun_skips_fault(self):
        with Archive() as archive:
            archive.put_model("m", counter())
            task = archive.create_staged_task(
                "r", [("m", None, 1), ("m", None, 1)], {"running": 1})
            report = archive.run_staged_task(task)

            def fault(stage):
                raise AssertionError("fault must not run on successful rerun")
            self.assertEqual(archive.run_staged_task(task, fault=fault), report)

    # ------------------------------------------------------------------
    # 9. Split equivalence against the original single-stage SDK1 analysis.
    # ------------------------------------------------------------------
    def test_split_matches_single_stage_sdk1(self):
        model = {"metrics": ["m", "n"],
                 "states": [{"id": "a", "reward": ["1/2", -1]},
                            {"id": "b", "reward": [0, 2]}],
                 "events": [
                     {"id": "ab", "source": "a", "target": "b", "rate": 2, "reward": [1, -1]},
                     {"id": "ba", "source": "b", "target": "a", "rate": 1, "reward": [0, 1]},
                     {"id": "self", "source": "b", "target": "b", "rate": 1, "reward": [-1, 2]}]}
        initial = {"a": "3/4", "b": "1/4"}
        split = (3, 2)  # total 5 <= 8, each stage inside [0,8]
        with Archive() as archive:
            archive.put_model("m", model)
            single = analyze(model, initial, sum(split))
            task = archive.create_staged_task(
                "split", [("m", None, split[0]), ("m", None, split[1])], initial)
            stats = archive.run_staged_task(task)["statistics"]
            self.assertVectorClose(stats["total_means"], single["means"])
            self.assertMatrixClose(stats["total_covariance"], single["covariance"])
            self.assertVectorClose(stats["probabilities"], single["probabilities"])
            # The stage-one block itself is the SDK1 report over duration 3.
            first_only = analyze(model, initial, split[0])
            self.assertMatrixClose(
                [row[:2] for row in stats["covariance"][:2]], first_only["covariance"])
            # Cross blocks must generally be nonzero for this interacting chain.
            self.assertTrue(any(abs(stats["covariance"][i][2 + j]) > 1e-6
                                for i in range(2) for j in range(2)))

    def test_split_with_zero_stage_matches_single(self):
        model = counter(1, (1, 1), ("1/2", -1))
        with Archive() as archive:
            archive.put_model("m", model)
            for split in [(0, 4), (4, 0)]:
                single = analyze(model, {"running": 1}, 4)
                task = archive.create_staged_task(
                    f"s{split}", [("m", None, split[0]), ("m", None, split[1])],
                    {"running": 1})
                stats = archive.run_staged_task(task)["statistics"]
                self.assertVectorClose(stats["total_means"], single["means"])
                self.assertMatrixClose(stats["total_covariance"], single["covariance"])
                self.assertVectorClose(stats["probabilities"], single["probabilities"])

    # ------------------------------------------------------------------
    # 10. Per-stage numerical domain boundaries.
    # ------------------------------------------------------------------
    def test_domain_boundaries(self):
        model = counter()
        with Archive() as archive:
            archive.put_model("m", model)
            # Durations 0 and 8 are legal on either stage; sums may exceed 8.
            for durations in [(0, 0), (8, 8), (8, 0), (0, 8)]:
                task = archive.create_staged_task(
                    f"d{durations}", [("m", None, durations[0]), ("m", None, durations[1])],
                    {"running": 1})
                stats = archive.run_staged_task(task)["statistics"]
                self.assertEqual(len(stats["covariance"]), 4)
            for bad in [-0.01, 8.01]:
                with self.assertRaises(ValidationError):
                    archive.create_staged_task(
                        f"b{bad}", [("m", None, bad), ("m", None, 1)], {"running": 1})
                with self.assertRaises(ValidationError):
                    archive.create_staged_task(
                        f"c{bad}", [("m", None, 1), ("m", None, bad)], {"running": 1})
            # Booleans are not integers for revisions or task ids.
            with self.assertRaises(ValidationError):
                archive.create_staged_task(
                    "boolrev", [("m", True, 1), ("m", None, 1)], {"running": 1})
            with self.assertRaises(ValidationError):
                archive.get_staged_task(True)
            # Floats are rejected as probabilities.
            with self.assertRaises(ValidationError):
                archive.create_staged_task(
                    "floatp", [("m", None, 1), ("m", None, 1)], {"running": 1.0})
            # Rational outside |8| and oversized denominators rejected.
            with self.assertRaises(ValidationError):
                archive.create_staged_task(
                    "big", [("m", None, 1), ("m", None, 1)], {"running": "9"})
            with self.assertRaises(ValidationError):
                archive.create_staged_task(
                    "den", [("m", None, 1), ("m", None, 1)],
                    {"running": "1/1000001"})

    # ------------------------------------------------------------------
    # 11. Replay, conflict and request namespaces.
    # ------------------------------------------------------------------
    def test_replay_conflict_and_namespace(self):
        with Archive() as archive:
            archive.put_model("m", counter())
            archive.put_cohort("c", ["running"], {"running": 1}, 1)
            legacy = archive.create_task("shared", "m", "c")
            staged = archive.create_staged_task(
                "shared", [("m", None, 1), ("m", None, 1)], {"running": 1})
            self.assertEqual(legacy, staged)  # numeric ids may coincide
            # list/tuple and rational spellings are equivalent.
            again = archive.create_staged_task(
                "shared", (("m", None, 1.0), ("m", None, 1.0)),
                {"running": Fraction(1)})
            self.assertEqual(again, staged)
            # None vs explicit revision is a conflict.
            with self.assertRaises(RequestConflict):
                archive.create_staged_task(
                    "shared", [("m", 1, 1), ("m", None, 1)], {"running": 1})
            with self.assertRaises(RequestConflict):
                archive.create_staged_task(
                    "shared", [("m", None, 1), ("m", None, 2)], {"running": 1})
            # A distribution that does not normalize is rejected as invalid
            # before the stored replay binding is consulted.
            with self.assertRaises(ValidationError):
                archive.create_staged_task(
                    "shared", [("m", None, 1), ("m", None, 1)],
                    {"running": "1/2"})
            # Illegal shapes fail before any replay comparison.
            with self.assertRaises(ValidationError):
                archive.create_staged_task("shared", [("m", None, 1)], {"running": 1})
            # Unknown identities.
            with self.assertRaises(NotFound):
                archive.create_staged_task(
                    "x", [("ghost", None, 1), ("m", None, 1)], {"running": 1})
            with self.assertRaises(NotFound):
                archive.create_staged_task(
                    "y", [("m", 99, 1), ("m", None, 1)], {"running": 1})
            with self.assertRaises(NotFound):
                archive.get_staged_task(4242)

    # ------------------------------------------------------------------
    # 12. Persistence, receipts, detached values and legacy compatibility.
    # ------------------------------------------------------------------
    def test_persistence_receipt_and_detach(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "a.sqlite")
            with Archive(path) as archive:
                archive.put_model("m", counter())
                task = archive.create_staged_task(
                    "r", [("m", None, 1), ("m", None, 1)], {"running": 1})
                report = archive.run_staged_task(task)
                # Receipt is the SHA-256 of the canonical payload sans receipt.
                import hashlib
                from analysis_archive.validation import dumps
                payload = dumps({k: v for k, v in report.items() if k != "receipt"})
                self.assertEqual(report["receipt"],
                                 hashlib.sha256(payload.encode("utf-8")).hexdigest())
                self.assertEqual(archive.get_staged_result(task), report)
                detached = archive.get_staged_result(task)
                detached["statistics"]["means"][0] = 12345
                self.assertEqual(archive.get_staged_result(task), report)
                view = archive.get_staged_task(task)
                view["parameters"]["initial"]["running"] = "0"
                self.assertEqual(
                    archive.get_staged_task(task)["parameters"]["initial"]["running"], "1")
            with Archive(path) as archive:
                self.assertEqual(archive.get_staged_result(task), report)
                self.assertEqual(archive.run_staged_task(task), report)

    def test_legacy_archive_keeps_old_data_and_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.sqlite")
            ids = build_legacy(path)
            with Archive(path) as archive:
                done = [t for t in ids.values()
                        if archive.get_task(t)["status"] == "succeeded"]
                receipts_before = {t: archive.get_result(t)["receipt"] for t in done}
                integrity = archive.store.connection.execute("PRAGMA integrity_check").fetchone()[0]
                self.assertEqual(integrity, "ok")
                self.assertEqual(
                    archive.store.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                # New staged capability is available on the untouched archive.
                task = archive.create_staged_task(
                    "staged", [("press", 1, 1), ("press", 2, 1)], {"running": 1})
                staged_report = archive.run_staged_task(task)
            with Archive(path) as archive:
                self.assertEqual(
                    {t: archive.get_result(t)["receipt"] for t in done}, receipts_before)
                self.assertEqual(archive.get_staged_result(task), staged_report)
                # Old pending task still pending and still runnable.
                self.assertEqual(archive.get_task(ids["pending"])["status"], "pending")
                archive.run_task(ids["pending"])

    # ------------------------------------------------------------------
    # 13. Engine independence: terminal-conditioned moments reconcile to SDK1.
    # ------------------------------------------------------------------
    def test_engine_moments_reconcile_to_sdk1(self):
        models = [
            counter(), counter(3, (1, -1)), parallel(),
            {"metrics": ["m"],
             "states": [{"id": "a", "reward": ["1/2"]}, {"id": "b", "reward": [-1]}],
             "events": [
                 {"id": "ab", "source": "a", "target": "b", "rate": "3/2", "reward": ["2"]},
                 {"id": "ba", "source": "b", "target": "a", "rate": 1, "reward": ["-1"]}]}]
        for model in models:
            state_ids = [s["id"] for s in model["states"]]
            k = len(model["metrics"])
            for duration in [0, Fraction(1, 2), 1, 3, 8]:
                initial = {s: Fraction(i + 1, sum(range(1, len(state_ids) + 1)))
                           for i, s in enumerate(state_ids)}
                initial = {s: str(v / sum(initial.values())) for s, v in initial.items()}
                sdk = analyze(model, initial, float(duration))
                engine = stage_moments(model, initial, float(duration))
                self.assertVectorClose(engine["means"], sdk["means"])
                self.assertVectorClose(engine["probabilities"], sdk["probabilities"])
                self.assertMatrixClose(engine["covariance"], sdk["covariance"])
                # Conditional moments weighted by terminal masses equal the
                # unconditional mean -- an independent law-of-total-expectation.
                for i in range(k):
                    weighted = sum(engine["probabilities"][a]
                                   * engine["terminal_means"][a][i]
                                   for a in range(len(state_ids)))
                    self.assertTrue(close(weighted, engine["means"][i]),
                                    (duration, weighted, engine["means"][i]))


if __name__ == "__main__":
    unittest.main()
