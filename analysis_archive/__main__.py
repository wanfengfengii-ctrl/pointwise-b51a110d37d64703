import argparse
import json
import tempfile
from pathlib import Path
from . import Archive, ResultNotReady
from .samples import build_legacy


def demo():
    with tempfile.TemporaryDirectory(prefix="equipment-analysis-") as directory:
        path = Path(directory) / "archive.sqlite"
        ids = build_legacy(path)
        with Archive(path) as archive:
            original = archive.get_result(ids["completed"])
            def fail(stage):
                if stage == "before_commit":
                    raise RuntimeError("demo interruption")
            try:
                archive.run_task(ids["pending"], fault=fail)
            except RuntimeError as exc:
                if str(exc) != "demo interruption":
                    raise
            else:
                raise AssertionError("fault did not fire")
            assert archive.get_task(ids["pending"])["status"] == "pending"
            try:
                archive.get_result(ids["pending"])
            except ResultNotReady:
                pass
            else:
                raise AssertionError("partial result published")
        with Archive(path) as archive:
            retried = archive.run_task(ids["pending"])
            assert archive.run_task(ids["pending"]) == retried
            assert archive.get_result(ids["completed"]) == original
            return {"historical": original, "revised": archive.get_result(ids["revised"]),
                    "stopping": archive.get_result(ids["stopping"]),
                    "parallel": archive.get_result(ids["mixed"]),
                    "retry": retried, "failure_published_result": False,
                    "task_count": archive.store.connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Equipment cumulative reward archive")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo")
    seed = commands.add_parser("seed", help="build a complete old-format sample archive")
    seed.add_argument("database")
    for name in ["run", "result"]:
        command = commands.add_parser(name)
        command.add_argument("database")
        command.add_argument("task", type=int)
    arguments = parser.parse_args(argv)
    if arguments.command == "demo":
        output = demo()
    elif arguments.command == "seed":
        output = build_legacy(arguments.database)
    else:
        with Archive(arguments.database) as archive:
            output = archive.run_task(arguments.task) if arguments.command == "run" else archive.get_result(arguments.task)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
