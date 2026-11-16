"""Fixed two-stage plans: validation, atomic binding, replay and snapshots."""
import json
from fractions import Fraction
from . import validation as v
from .errors import NotFound, RequestConflict, ValidationError


class StagedTasks:
    def __init__(self, store, models):
        self.store, self.models = store, models

    def _canonical(self, request_id, stages, initial):
        """Normalize explicit arguments before any stored binding is consulted."""
        request_id = v.identifier(request_id)
        if not isinstance(stages, (list, tuple)) or len(stages) != 2:
            raise ValidationError("stages must contain exactly two entries")
        plan, binding_stages = [], []
        for entry in stages:
            if not isinstance(entry, (list, tuple)) or len(entry) != 3:
                raise ValidationError("each stage must be (model_id, revision, duration)")
            model_id, revision, raw_duration = entry
            model_id = v.identifier(model_id)
            if revision is not None and (type(revision) is not int or revision <= 0):
                raise ValidationError("revision must be a positive integer or None")
            duration = v.duration(raw_duration)
            plan.append({"model_id": model_id, "revision": revision, "duration": duration})
            binding_stages.append({"model_id": model_id, "revision": revision,
                                   "duration": duration})
        if not isinstance(initial, dict):
            raise ValidationError("initial distribution must be a mapping")
        for state_id in initial:
            v.identifier(state_id)
        probabilities = {state_id: v.rational(value) for state_id, value in initial.items()}
        if any(value < 0 for value in probabilities.values()):
            raise ValidationError("initial probabilities must be nonnegative")
        if sum(probabilities.values(), Fraction(0)) != 1:
            raise ValidationError("initial probabilities must sum exactly to one")
        full_initial = {state_id: str(value) for state_id, value in probabilities.items()}
        # Replay binding strips zero entries so equivalent spellings collide;
        # the full map is still checked against the fixed state directory.
        canonical_initial = {state_id: text for state_id, text in full_initial.items()
                             if text != "0"}
        binding = v.dumps({"stages": binding_stages, "initial": canonical_initial})
        return request_id, plan, full_initial, binding

    def create_staged_task(self, request_id, stages, initial):
        request_id, plan, full_initial, binding = self._canonical(request_id, stages, initial)
        with self.store.transaction() as db:
            old = db.execute("SELECT task_id,binding FROM staged_tasks WHERE request_id=?",
                             (request_id,)).fetchone()
            if old is not None:
                if old["binding"] != binding:
                    raise RequestConflict("request identity already bound to different arguments")
                return old["task_id"]
            resolved = []
            definitions = []
            for entry in plan:
                number, definition = self.models._resolve(db, entry["model_id"], entry["revision"])
                resolved.append({**entry, "revision": number})
                definitions.append(definition)
            first_states = [s["id"] for s in definitions[0]["states"]]
            second_states = [s["id"] for s in definitions[1]["states"]]
            if set(first_states) != set(second_states):
                raise ValidationError("the two stages must share the same state-ID set")
            first_metrics = definitions[0]["metrics"]
            if set(first_metrics) != set(definitions[1]["metrics"]):
                raise ValidationError("the two stages must share the same metric-ID set")
            if set(full_initial) - set(first_states):
                raise ValidationError("unknown initial state")
            initial_snapshot = {state_id: full_initial.get(state_id, "0")
                                for state_id in first_states}
            parameters = {"states": first_states, "metrics": list(first_metrics),
                          "initial": initial_snapshot}
            cursor = db.execute(
                "INSERT INTO staged_tasks(request_id,binding,plan,parameters,status)"
                " VALUES(?,?,?,?,'pending')",
                (request_id, binding, v.dumps({"stages": resolved}), v.dumps(parameters)))
            return cursor.lastrowid

    def _row(self, db, task_id):
        if type(task_id) is not int or task_id <= 0:
            raise ValidationError("task identity must be a positive integer")
        row = db.execute("SELECT * FROM staged_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise NotFound("staged task")
        return row

    def get_staged_task(self, task_id):
        row = self._row(self.store.connection, task_id)
        plan = json.loads(row["plan"])
        parameters = json.loads(row["parameters"])
        return {"task_id": row["task_id"], "request_id": row["request_id"],
                "stages": plan["stages"], "parameters": parameters, "status": row["status"]}
