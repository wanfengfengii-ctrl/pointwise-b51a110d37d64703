import json
from . import validation as v
from .errors import NotFound


class Cohorts:
    def __init__(self, store):
        self.store = store

    def put_cohort(self, cohort_id, states, initial, duration):
        cohort_id = v.identifier(cohort_id)
        payload = v.dumps(v.cohort(states, initial, duration))
        with self.store.transaction() as db:
            db.execute("""INSERT INTO cohorts VALUES(?,?)
                       ON CONFLICT(cohort_id) DO UPDATE SET payload=excluded.payload""", (cohort_id, payload))
        return self.get_cohort(cohort_id)

    def get_cohort(self, cohort_id):
        v.identifier(cohort_id)
        row = self.store.connection.execute("SELECT payload FROM cohorts WHERE cohort_id=?", (cohort_id,)).fetchone()
        if row is None:
            raise NotFound("cohort")
        return json.loads(row[0])
