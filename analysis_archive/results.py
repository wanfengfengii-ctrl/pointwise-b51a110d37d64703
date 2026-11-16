import hashlib
import json
from .validation import dumps
from .errors import ResultNotReady


class Results:
    def __init__(self, store, tasks):
        self.store, self.tasks = store, tasks

    def _get(self, db, task_id):
        task = self.tasks._row(db, task_id)
        if task["status"] != "succeeded":
            raise ResultNotReady("task has no published result")
        row = db.execute("SELECT payload,receipt FROM results WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise RuntimeError("archive invariant violated: succeeded task without result")
        return {**json.loads(row["payload"]), "receipt": row["receipt"]}

    def get_result(self, task_id):
        with self.store.transaction() as db:
            return self._get(db, task_id)

    def _publish(self, db, task, statistics, fault):
        payload = dumps({"task_id": task["task_id"], "request_id": task["request_id"],
                         "model_id": task["model_id"], "revision": task["revision"],
                         "parameters": json.loads(task["parameters"]), "statistics": statistics})
        receipt = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        db.execute("INSERT INTO results VALUES(?,?,?)", (task["task_id"], payload, receipt))
        fault("after_result_insert")
        db.execute("UPDATE tasks SET status='succeeded' WHERE task_id=?", (task["task_id"],))
        fault("before_commit")
        return self._get(db, task["task_id"])
