import json
from vendor.sdk1 import analyze
from . import validation as v


class Analysis:
    def __init__(self, store, models, tasks, results):
        self.store, self.models, self.tasks, self.results = store, models, tasks, results

    def state_probabilities(self, model_id, initial, duration, revision=None):
        with self.store.transaction() as db:
            _, definition = self.models._resolve(db, model_id, revision)
            parameters = v.cohort([s["id"] for s in definition["states"]], initial, duration)
            statistics = analyze(definition, parameters["initial"], parameters["duration"])
            return dict(zip(statistics["states"], statistics["probabilities"]))

    def run_task(self, task_id, *, fault=None):
        if fault is None:
            def fault(stage):
                return None
        with self.store.transaction() as db:
            task = self.tasks._row(db, task_id)
            if task["status"] == "succeeded":
                return self.results._get(db, task_id)
            _, definition = self.models._resolve(db, task["model_id"], task["revision"])
            parameters = json.loads(task["parameters"])
            statistics = analyze(definition, parameters["initial"], parameters["duration"])
            fault("after_compute")
            return self.results._publish(db, task, statistics, fault)
