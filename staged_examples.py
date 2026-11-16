"""Public data, analytic expectations and ordinary callers; no staged solver."""
import copy
import json
import math
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from analysis_archive import Archive, ResultNotReady


def definitions():
    first = {
        "metrics": ["count", "signed"],
        "states": [{"id": "on", "reward": [0, 0]},
                   {"id": "off", "reward": [0, 0]}],
        "events": [{"id": "stop", "source": "on", "target": "off",
                    "rate": 1, "reward": [1, 2]}]
    }
    second = {
        "metrics": ["signed", "count"],
        "states": [{"id": "off", "reward": [-1, 1]},
                   {"id": "on", "reward": [0, 0]}],
        "events": []
    }
    return first, second


def parameters(initial_on="1"):
    q = Fraction(initial_on)
    return [("operating", None, 1), ("maintenance", None, 1)], {
        "on": str(q), "off": str(1 - q)
    }


def install(archive):
    first, second = definitions()
    archive.put_model("operating", first)
    archive.put_model("maintenance", second)


def expected(initial_on="1"):
    # A = first-stage stop count; B = second-stage time in off (duration 1).
    # A implies B=1; an initially off device has A=0, B=1.
    q = float(Fraction(initial_on))
    survival = math.exp(-1)
    a, b = q * (1 - survival), 1 - q * survival
    va, vb, cab = a * (1 - a), b * (1 - b), a * (1 - b)
    means = [a, 2 * a, b, -b]
    terms = [(0, 1), (0, 2), (1, 1), (1, -1)]
    base = [[va, cab], [cab, vb]]
    covariance = [[ci * cj * base[i][j] for j, cj in terms] for i, ci in terms]
    total_means = [a + b, 2 * a - b]
    total_covariance = [
        [va + vb + 2 * cab, 2 * va - vb + cab],
        [2 * va - vb + cab, 4 * va + vb - 4 * cab]
    ]
    return {
        "states": ["on", "off"], "probabilities": [q * survival, b],
        "metrics": ["count", "signed"],
        "coordinates": [[1, "count"], [1, "signed"], [2, "count"], [2, "signed"]],
        "means": means, "covariance": covariance,
        "total_means": total_means, "total_covariance": total_covariance
    }


def assert_statistics(actual, target):
    def compare(a, b):
        if isinstance(b, list):
            assert isinstance(a, list) and len(a) == len(b), (a, b)
            for x, y in zip(a, b):
                compare(x, y)
        elif isinstance(b, float):
            assert type(a) is float and math.isfinite(a), a
            assert abs(a - b) <= 2e-8 + 2e-8 * abs(b), (a, b)
        else:
            assert a == b, (a, b)
    for key, value in target.items():
        compare(actual[key], value)


def check():
    reports = []
    for q in ["1", "1/2", "0"]:
        with Archive() as archive:
            install(archive)
            stages, initial = parameters(q)
            task = archive.create_staged_task("sample", stages, initial)
            report = archive.run_staged_task(task)
            assert_statistics(report["statistics"], expected(q))
            assert archive.get_staged_result(task) == report
            assert archive.run_staged_task(task) == report
            reports.append(report)
    return reports


class DemonstrationFailure(RuntimeError):
    pass


def demo():
    with tempfile.TemporaryDirectory(prefix="staged-equipment-") as directory:
        path = Path(directory) / "archive.sqlite"
        with Archive(path) as archive:
            install(archive)
            stages, initial = parameters()
            task = archive.create_staged_task("fixed", stages, initial)
            original = archive.get_staged_task(task)
            stages[0] = ("operating", 1, 0)
            initial["on"] = "0"
            first, second = definitions()
            first["events"][0]["rate"] = 2
            first["states"].reverse()
            second["states"].reverse()
            archive.put_model("operating", first)
            archive.put_model("maintenance", second)
            same_stages, same_initial = parameters()
            assert archive.create_staged_task("fixed", same_stages, same_initial) == task
            assert archive.get_staged_task(task) == original
            detached = archive.get_staged_task(task)
            detached["stages"][0]["duration"] = 0
            assert archive.get_staged_task(task) == original

            def fault(stage):
                if stage == "before_commit":
                    raise DemonstrationFailure(stage)
            try:
                archive.run_staged_task(task, fault=fault)
            except DemonstrationFailure:
                pass
            else:
                raise AssertionError("publication fault did not fire")
            assert archive.get_staged_task(task)["status"] == "pending"
            try:
                archive.get_staged_result(task)
            except ResultNotReady:
                pass
            else:
                raise AssertionError("partial result visible")
        with Archive(path) as archive:
            historical = archive.run_staged_task(task)
            assert_statistics(historical["statistics"], expected())
            newer = archive.create_staged_task("new", *parameters())
            revised = archive.run_staged_task(newer)
            assert [s["revision"] for s in historical["stages"]] == [1, 1]
            assert [s["revision"] for s in revised["stages"]] == [2, 2]
            assert abs(revised["statistics"]["means"][0] - (1 - math.exp(-2))) < 2e-8
            snapshot = copy.deepcopy(historical)
            historical["statistics"]["means"][0] = 99
            assert archive.get_staged_result(task) == snapshot
        with Archive(path) as archive:
            assert archive.run_staged_task(task) == snapshot
            assert archive.get_staged_result(task) == snapshot
            return {"historical": snapshot, "revised": revised,
                    "rollback_then_reopen": True}


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else None
    if mode == "reference":
        first, second = definitions()
        output = {"definitions": [first, second],
                  "parameters": parameters(), "expected": expected(),
                  "random_initial": {"parameters": parameters("1/2"),
                                     "expected": expected("1/2")}}
    elif mode == "check":
        output = check()
    elif mode == "demo":
        output = demo()
    else:
        raise SystemExit("usage: python3 -B staged_examples.py reference|check|demo")
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
