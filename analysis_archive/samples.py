"""Small reproducible equipment models and complete version-one archive builder."""
from . import Archive


def counter(rate=2, event_reward=(1, 2), state_reward=(0, 0)):
    return {"metrics": ["cycles", "load"],
            "states": [{"id": "running", "reward": list(state_reward)}],
            "events": [{"id": "pulse", "source": "running", "target": "running",
                        "rate": rate, "reward": list(event_reward)}]}


def stopped():
    return {"metrics": ["uptime", "stops"],
            "states": [{"id": "on", "reward": [1, 0]}, {"id": "off", "reward": [0, 0]}],
            "events": [{"id": "stop", "source": "on", "target": "off", "rate": 2, "reward": [0, 1]}]}


def parallel():
    return {"metrics": ["net", "signed"],
            "states": [{"id": "a", "reward": ["1/2", -1]}, {"id": "b", "reward": [0, 2]}],
            "events": [
                {"id": "small", "source": "a", "target": "b", "rate": 1, "reward": [1, -1]},
                {"id": "large", "source": "a", "target": "b", "rate": 2, "reward": [3, 1]},
                {"id": "heartbeat", "source": "b", "target": "b", "rate": 1, "reward": [-1, 2]}]}


def build_legacy(path):
    """Populate an EMPTY archive; return stable task IDs. Does not overwrite data."""
    with Archive(path) as archive:
        if archive.store.connection.execute("SELECT COUNT(*) FROM model_revisions").fetchone()[0]:
            raise ValueError("sample builder requires an empty archive")
        archive.put_model("press", counter())
        archive.put_cohort("shift", ["running"], {"running": 1}, 1)
        completed = archive.create_task("historical-press", "press", "shift")
        archive.run_task(completed)
        pending = archive.create_task("pending-press", "press", "shift")
        archive.put_model("press", counter(3, (1, -1)))
        archive.put_cohort("shift", ["running"], {"running": 1}, 2)
        revised = archive.create_task("revised-press", "press", "shift")
        archive.run_task(revised)
        archive.put_model("stopping", stopped())
        archive.put_cohort("start-on", ["on", "off"], {"on": 1}, 1)
        stopping = archive.create_task("stopping-run", "stopping", "start-on")
        archive.run_task(stopping)
        archive.put_model("mixed", parallel())
        archive.put_cohort("mixed-start", ["a", "b"], {"a": "3/4", "b": "1/4"}, "3/2")
        mixed = archive.create_task("mixed-run", "mixed", "mixed-start")
        archive.run_task(mixed)
        return {"completed": completed, "pending": pending, "revised": revised,
                "stopping": stopping, "mixed": mixed}
