import sqlite3
from contextlib import contextmanager
from .errors import ArchiveError

SCHEMA = (
    "CREATE TABLE archive_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "INSERT INTO archive_meta VALUES('format','1')",
    """CREATE TABLE model_revisions(
       model_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
       payload TEXT NOT NULL, PRIMARY KEY(model_id,revision))""",
    """CREATE TABLE model_heads(
       model_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
       FOREIGN KEY(model_id,revision) REFERENCES model_revisions(model_id,revision))""",
    "CREATE TABLE cohorts(cohort_id TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    """CREATE TABLE tasks(
       task_id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
       binding TEXT NOT NULL, model_id TEXT NOT NULL, revision INTEGER NOT NULL,
       parameters TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','succeeded')),
       FOREIGN KEY(model_id,revision) REFERENCES model_revisions(model_id,revision))""",
    """CREATE TABLE results(
       task_id INTEGER PRIMARY KEY REFERENCES tasks(task_id),
       payload TEXT NOT NULL, receipt TEXT NOT NULL UNIQUE)""",
)

# Fixed two-stage plans live in their own namespace and tables. They are added
# transparently when an older version-one archive is opened, so legacy data,
# tasks and receipts remain byte-for-byte untouched.
STAGED_SCHEMA = (
    """CREATE TABLE staged_tasks(
       task_id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
       binding TEXT NOT NULL, plan TEXT NOT NULL, parameters TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN ('pending','succeeded')))""",
    """CREATE TABLE staged_results(
       task_id INTEGER PRIMARY KEY REFERENCES staged_tasks(task_id),
       payload TEXT NOT NULL, receipt TEXT NOT NULL UNIQUE)""",
)


class Store:
    """Single-worker SQLite owner; transaction is also available to storage adapters."""

    def __init__(self, path):
        self.connection = sqlite3.connect(str(path), isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        try:
            with self.transaction() as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables:
                    for statement in SCHEMA:
                        db.execute(statement)
                elif "archive_meta" not in tables:
                    raise ArchiveError("not an analysis archive")
                # Add the fixed two-stage tables to legacy archives in place.
                # Existing version-one rows and schema objects are never altered.
                if "staged_tasks" not in tables:
                    for statement in STAGED_SCHEMA:
                        db.execute(statement)
                row = db.execute("SELECT value FROM archive_meta WHERE key='format'").fetchone()
                if row is None or row[0] != "1":
                    raise ArchiveError("unsupported archive format")
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def transaction(self):
        db = self.connection
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def close(self):
        self.connection.close()
