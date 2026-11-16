from .storage import Store
from .models import Models
from .cohorts import Cohorts
from .tasks import Tasks
from .results import Results
from .analysis import Analysis
from .errors import ArchiveError, ValidationError, NotFound, RequestConflict, ResultNotReady

__all__ = ["Archive", "ArchiveError", "ValidationError", "NotFound", "RequestConflict", "ResultNotReady"]


class Archive:
    def __init__(self, path=":memory:"):
        self.store = Store(path)
        self.models = Models(self.store)
        self.cohorts = Cohorts(self.store)
        self.tasks = Tasks(self.store, self.models)
        self.results = Results(self.store, self.tasks)
        self.analysis = Analysis(self.store, self.models, self.tasks, self.results)

    def put_model(self, model_id, definition):
        return self.models.put_model(model_id, definition)

    def get_model(self, model_id, revision=None):
        return self.models.get_model(model_id, revision)

    def revisions(self, model_id):
        return self.models.revisions(model_id)

    def put_cohort(self, cohort_id, states, initial, duration):
        return self.cohorts.put_cohort(cohort_id, states, initial, duration)

    def get_cohort(self, cohort_id):
        return self.cohorts.get_cohort(cohort_id)

    def create_task(self, request_id, model_id, cohort_id, revision=None):
        return self.tasks.create_task(request_id, model_id, cohort_id, revision)

    def get_task(self, task_id):
        return self.tasks.get_task(task_id)

    def state_probabilities(self, model_id, initial, duration, revision=None):
        return self.analysis.state_probabilities(model_id, initial, duration, revision)

    def run_task(self, task_id, *, fault=None):
        return self.analysis.run_task(task_id, fault=fault)

    def get_result(self, task_id):
        return self.results.get_result(task_id)

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
