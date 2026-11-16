# Equipment cumulative reward archive

Python 3.11 standard library only. This archive turns equipment operating models and initial fleet distributions into reproducible joint cumulative reports. A report contains final state probabilities, metric means and the full covariance matrix. Model revisions, task inputs, results and receipts remain available after reopening SQLite.

The operational flow is: register a model, save a cohort, create a request-bound task, run it, then retrieve the report. Correct an event rate by adding a revision under the same model ID. Existing tasks keep their original revision and cohort snapshot; a new request uses the current revision. An unsuccessful calculation or publication leaves the task pending and retryable.

## Run

From the project directory:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m analysis_archive demo
python3 -B -m analysis_archive seed example.sqlite
python3 -B -m analysis_archive run example.sqlite 2
python3 -B -m analysis_archive result example.sqlite 2
```

The demo uses and removes a test temporary directory. It builds a real version-one archive, reports both historical and revised models, injects a pre-commit failure, closes the database and retries after reopening. The seed command requires an archive with no model revisions and returns the actual task IDs; in a fresh archive the pending task is 2. It never deletes or overwrites a user's database.

## Model and numerical contract

Model definitions have three required fields:

* `metrics`: an ordered directory of 1–3 unique metric IDs.
* `states`: an ordered directory of 1–6 objects, each with a unique `id` and a `reward` vector in metric order. A state reward is a rate integrated over residence time.
* `events`: up to 18 objects with unique `id`, `source`, `target`, nonnegative `rate`, and a `reward` vector paid once per event.

IDs are nonempty Unicode strings of at most 64 characters. Event endpoints must reference the revision's states. A self event changes no state but still occurs and pays its entire vector. Parallel events are distinct causes. Zero-rate events retain their identities. The same event's metric payments share one occurrence; they are not independent counts. Negative rewards are valid.

The frozen numerical domain uses normalized time units: duration is finite in [0,8]; each state's total event intensity, including self events, is at most 8; each reward component has absolute value at most 8. Rates, rewards and initial probabilities accept integers, Fraction objects or rational strings, with reduced denominator at most 1,000,000; floats and booleans are rejected for those quantities. Duration additionally accepts floats. Initial probabilities must sum to exactly one as rational numbers, be nonnegative, and mention no unknown states; omitted states have probability zero. Reward-vector lengths must match the metric directory. The independent numerical acceptance rule is `abs(a-b) <= 2e-8 + 2e-8*abs(b)` (the tests use the slightly stricter math.isclose rule). Returned values are binary64 floats.

For a path X and events e, each metric is its state-rate integral plus the sum of its event payments. Covariance is the population covariance of those random cumulative values. It includes dependence between residence and event counts, between metrics, and between random initial states. Time zero returns the initial probabilities and zero cumulative statistics. An absorbing state can still have a residence reward. State and metric output order always follows the saved revision.

## Public Python API

```python
from analysis_archive import Archive
from analysis_archive.samples import counter

with Archive("equipment.sqlite") as archive:
    revision = archive.put_model("press", counter())
    archive.put_cohort("morning", ["running"], {"running": 1}, 1)
    task = archive.create_task("request-001", "press", "morning")
    report = archive.run_task(task)
    assert archive.get_result(task) == report
```

Archive defaults to `:memory:`. It supports close() and the context manager. Its methods delegate to the real models, cohorts, tasks, analysis and results responsibilities in the corresponding analysis_archive modules.

* `put_model(model_id, definition) -> int`: validate the entire definition, append an immutable revision starting at 1, and atomically replace the current pointer. Even identical definitions create separate revisions. Invalid input does not advance the pointer.
* `get_model(model_id, revision=None) -> dict`: return model_id, revision and a detached canonical definition. Rational values in saved definitions are reduced strings. `revisions(model_id) -> list[int]` lists all revisions in order.
* `put_cohort(cohort_id, states, initial, duration) -> dict`: atomically replace a complete cohort definition; return its detached canonical parameters. A cohort's state directory is independent of a model name. `get_cohort(cohort_id)` returns it. A task requires the cohort and its fixed revision to have exactly the same state-ID set; directory order may differ.
* `create_task(request_id, model_id, cohort_id, revision=None) -> int`: save a fixed model revision and a full cohort snapshot. Request replay compares the explicit arguments (including whether revision was None), before resolving today's model or cohort. An identical replay returns the original task, even after a model or cohort correction. Changed arguments with the same request ID raise RequestConflict, including switching from None to an explicit revision.
* `get_task(task_id) -> dict`: return task_id, request_id, model_id, revision, parameters and status. Task IDs are positive integers. Status is pending or succeeded.
* `state_probabilities(model_id, initial, duration, revision=None) -> dict[state_id, float]`: compute final state probabilities for the selected revision without creating a task. It applies the same input validation and SDK1 semantics.
* `run_task(task_id, *, fault=None) -> dict`: compute pending work through SDK1, then publish its complete result and succeeded status in one transaction. Repeating successful execution returns the saved report without recomputing or invoking fault.
* `get_result(task_id) -> dict`: retrieve the complete report. A report has task_id, request_id, model_id, revision, parameters, statistics and receipt. Statistics has metrics, states, probabilities, means and covariance. Receipt is the SHA-256 of the canonical report payload before the receipt field is added. It identifies a particular publication, not an approximate equality class of numerical results.

Public exceptions are ValidationError, NotFound, RequestConflict and ResultNotReady, all derived from ArchiveError. Unknown models, revisions, cohorts and tasks raise NotFound. An existing pending task has no retrievable result and raises ResultNotReady. Validation failures reject the entire operation. User callback and numerical errors propagate; they do not publish partial results. Returned dictionaries are detached from storage.

The optional synchronous fault(stage) callback runs at after_compute, after_result_insert and before_commit. Raising at any of these points rolls back both result and task terminal state. It is a public deterministic failure-injection boundary. No callback is run after commit. A successful repeated request or task execution returns the original identity or report.

## Storage and calculation

analysis_archive/storage.py contains the complete version-one schema and Store.transaction(). The tables are archive_meta, model_revisions, model_heads, cohorts, tasks and results. Revisions contain complete JSON model definitions, including stable state/event IDs, rates and signed reward vectors. Tasks have explicit model/revision foreign keys, their request binding, canonical cohort snapshot and status. Results retain the complete payload and unique receipt. Foreign keys are enabled for every connection. The full sample builder in analysis_archive/samples.py creates two press revisions, completed and pending historical tasks, cohort correction, parallel events, self events and mixed residence/event models.

The supported deployment is a single worker per archive. Every write uses BEGIN IMMEDIATE and rollback on exceptions. Model registration, task binding and result publication have separate atomic boundaries. Readers retrieve a whole saved report in one transaction. Processes can close and reopen after an exception; no persisted running state needs recovery. Multi-worker scheduling and online schema changes are outside this version's contract.

vendor/sdk1.py is the active offline reward dependency. It uses uniformization and conditional uniform tick times; it retains self and parallel event payments. Its public analyze(model, initial, duration) accepts the archive's canonical validated model and normalized inputs and returns the statistics object. SDK1 exposes no continuous reward-matrix conversion. vendor/sdk2.py is a separate general numerical dependency with propagate(matrix, vector, time), returning exp(time*matrix) times a column vector. It accepts square finite real matrices of dimension 1–96 and scaled infinity norm at most 65536, nonnegative finite time, and finite vectors; it does not mutate inputs. Shape/domain errors raise ValueError; nonfinite exponentials or failed convergence raise ArithmeticError. Its implementation is scaled Taylor expansion and matrix squaring. It has no archive, event or reward interfaces. The current archive uses SDK1 only.

## Regression coverage

 tests/test_business_flow.py exercises deterministic residence, input rejection, query errors, zero time, detached values and cohort snapshots. tests/test_revision_binding.py covers fixed revisions, current pointers and rejected registrations. tests/test_reward_semantics.py independently checks Poisson self events, vector payments, parallel signed events, a random initial cohort, an absorbing two-state model using integral formulas, and all supported dimension/intensity/time bounds. tests/test_task_lifecycle.py covers request replay, conflict, every fault boundary, actual disk rollback/reopen and the complete legacy archive. tests/test_sdk2.py independently checks the column convention, a Jordan block, a rotation, semigroup composition, dimensional support and a 70-digit Decimal reference. tests/test_demo.py invokes the real CLI entry point, seed builder and reports. No external service or dependency installation is needed.

## Fixed two-stage extension

The fixed two-stage capability defined in STAGED.md is additive: every original interface, table, receipt and test is unchanged. Four Archive methods are added:

* `create_staged_task(request_id, stages, initial) -> int`: atomically fix a plan of exactly two `(model_id, revision, duration)` entries (revision may be `None`, meaning the current revision at creation time) plus a normalized initial distribution over the first stage's states. The two pinned revisions must share state-ID and metric-ID sets, though directory order may differ. Replay compares normalized explicit arguments before resolving any model; changed arguments raise RequestConflict and malformed input raises ValidationError.
* `get_staged_task(task_id) -> dict`: return the frozen stages, parameters (first-stage state/metric directories and reduced rational initial probabilities) and pending/succeeded status.
* `run_staged_task(task_id, *, fault=None) -> dict`: continue one random path from the first stage's terminal state, pay nothing at the boundary, and publish stage means, the full stage-first covariance matrix (both cross blocks included) and same-metric totals with a SHA-256 receipt in one transaction. The same three fault points and rollback semantics as `run_task` apply.
* `get_staged_result(task_id) -> dict`: retrieve the published joint report; a pending task raises ResultNotReady.

Staged tasks use their own `staged_tasks`/`staged_results` tables and request namespace, so their numeric task ids may coincide with legacy task ids. Opening a legacy archive transparently adds the new tables without altering any version-one row; old reports need no backfill. The continuous calculation lives in `analysis_archive/staged_engine.py` (uniformization with terminal-state-conditioned first moments) and `analysis_archive/staged_analysis.py` (law-of-total-covariance join, with second-stage conditional runs reusing vendor.sdk1.analyze). Independent audits are in tests/test_staged_audit.py; see STAGED_HANDOFF.md for the fixed-identity to stage-statistics invariants and the audit rationale. The CLI demo now ends with the staged correction/fault/reopen demonstration wired through `staged_examples.demo()`.

