"""Publication of fixed two-stage joint reports and their receipts."""
import hashlib
import json
from .validation import dumps
from .errors import ResultNotReady


class StagedResults:
    def __init__(self, store, staged_tasks):
        self.store, self.staged_tasks = store, staged_tasks

    def _get(self, db, task_id):
        task = self.staged_tasks._row(db, task_id)
        if task["status"] != "succeeded":
            raise ResultNotReady("staged task has no published result")
        row = db.execute("SELECT payload,receipt FROM staged_results WHERE task_id=?",
                         (task_id,)).fetchone()
        if row is None:
            raise RuntimeError("archive invariant violated: succeeded staged task without result")
        return {**json.loads(row[0]), "receipt": row["receipt"]}

    def get_staged_result(self, task_id):
        with self.store.transaction() as db:
            return self._get(db, task_id)

    def _publish(self, db, task, report_without_receipt, fault):
        payload = dumps(report_without_receipt)
        receipt = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        db.execute("INSERT INTO staged_results VALUES(?,?,?)",
                   (task["task_id"], payload, receipt))
        fault("after_result_insert")
        db.execute("UPDATE staged_tasks SET status='succeeded' WHERE task_id=?",
                   (task["task_id"],))
        fault("before_commit")
        return self._get(db, task["task_id"])
