import json
from . import validation as v
from .errors import NotFound, RequestConflict, ValidationError


class Tasks:
    def __init__(self, store, models):
        self.store, self.models = store, models

    def create_task(self, request_id, model_id, cohort_id, revision=None):
        request_id, model_id, cohort_id = map(v.identifier, (request_id, model_id, cohort_id))
        if revision is not None and (type(revision) is not int or revision <= 0):
            raise ValidationError("revision must be a positive integer")
        binding = v.dumps({"model_id": model_id, "cohort_id": cohort_id, "revision": revision})
        with self.store.transaction() as db:
            old = db.execute("SELECT task_id,binding FROM tasks WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                if old["binding"] != binding:
                    raise RequestConflict("request identity already bound to different arguments")
                return old["task_id"]
            number, definition = self.models._resolve(db, model_id, revision)
            row = db.execute("SELECT payload FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone()
            if row is None:
                raise NotFound("cohort")
            parameters = json.loads(row[0])
            if set(parameters["states"]) != {s["id"] for s in definition["states"]}:
                raise ValidationError("cohort and model state directories differ")
            cursor = db.execute("""INSERT INTO tasks(request_id,binding,model_id,revision,parameters,status)
                                VALUES(?,?,?,?,?,'pending')""",
                                (request_id, binding, model_id, number, v.dumps(parameters)))
            return cursor.lastrowid

    def _row(self, db, task_id):
        if type(task_id) is not int or task_id <= 0:
            raise ValidationError("task identity must be a positive integer")
        row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise NotFound("task")
        return row

    def get_task(self, task_id):
        row = self._row(self.store.connection, task_id)
        return {"task_id": row["task_id"], "request_id": row["request_id"], "model_id": row["model_id"],
                "revision": row["revision"], "parameters": json.loads(row["parameters"]), "status": row["status"]}
