import json
from . import validation as v
from .errors import NotFound, ValidationError


class Models:
    def __init__(self, store):
        self.store = store

    def put_model(self, model_id, definition):
        model_id = v.identifier(model_id)
        payload = v.dumps(v.model(definition))
        with self.store.transaction() as db:
            row = db.execute("SELECT MAX(revision) FROM model_revisions WHERE model_id=?", (model_id,)).fetchone()
            revision = (row[0] or 0) + 1
            db.execute("INSERT INTO model_revisions VALUES(?,?,?)", (model_id, revision, payload))
            db.execute("""INSERT INTO model_heads VALUES(?,?)
                       ON CONFLICT(model_id) DO UPDATE SET revision=excluded.revision""",
                       (model_id, revision))
        return revision

    def _resolve(self, db, model_id, revision=None):
        v.identifier(model_id)
        if revision is not None and (type(revision) is not int or revision <= 0):
            raise ValidationError("revision must be a positive integer")
        if revision is None:
            head = db.execute("SELECT revision FROM model_heads WHERE model_id=?", (model_id,)).fetchone()
            if head is None:
                raise NotFound("model")
            revision = head[0]
        row = db.execute("SELECT payload FROM model_revisions WHERE model_id=? AND revision=?",
                         (model_id, revision)).fetchone()
        if row is None:
            raise NotFound("model revision")
        return revision, json.loads(row[0])

    def get_model(self, model_id, revision=None):
        with self.store.transaction() as db:
            number, definition = self._resolve(db, model_id, revision)
        return {"model_id": model_id, "revision": number, "definition": definition}

    def revisions(self, model_id):
        v.identifier(model_id)
        rows = self.store.connection.execute(
            "SELECT revision FROM model_revisions WHERE model_id=? ORDER BY revision", (model_id,)).fetchall()
        if not rows:
            raise NotFound("model")
        return [r[0] for r in rows]
