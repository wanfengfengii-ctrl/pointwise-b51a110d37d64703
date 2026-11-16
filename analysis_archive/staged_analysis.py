"""Joint continuous-path analysis over a fixed two-stage plan.

The two stages are joined along the same random path: the second stage starts
from the first stage's terminal state and no reward is paid at the boundary.
Stage-one cumulative amounts and the boundary state are kept dependent through
the terminal-state-conditioned first moments produced by staged_engine; the
cross-stage covariance then follows the law of total covariance rather than an
independence assumption. State and metric identities are joined by ID, so a
reversed directory in the second model cannot misalign the totals.
"""
import json
import math
from vendor.sdk1 import analyze
from .staged_engine import stage_moments


class StagedAnalysis:
    def __init__(self, store, models, staged_tasks, staged_results):
        self.store, self.models = store, models
        self.staged_tasks, self.staged_results = staged_tasks, staged_results

    @staticmethod
    def _joint_statistics(first_model, second_model, parameters, durations):
        first_states = [s["id"] for s in first_model["states"]]
        first_metrics = list(first_model["metrics"])
        initial = parameters["initial"]
        k = len(first_metrics)

        first = stage_moments(first_model, initial, durations[0])
        index1 = {state_id: a for a, state_id in enumerate(first["states"])}
        midx1 = {metric_id: i for i, metric_id in enumerate(first_model["metrics"])}

        second_states = [s["id"] for s in second_model["states"]]
        midx2 = {metric_id: i for i, metric_id in enumerate(second_model["metrics"])}
        # Deterministic one-state starts reuse the public SDK1 interface: each
        # run gives E[Y2 | X1(T)=a] and Cov(Y2 | X1(T)=a) in stage-two order.
        # Results are keyed by state ID so a reordered second directory cannot
        # misalign the boundary continuation.
        second_means, second_covariances, second_endpoints = {}, {}, {}
        for state_id in first_states:
            point_initial = {s: (1 if s == state_id else 0) for s in second_states}
            result = analyze(second_model, point_initial, durations[1])
            second_means[state_id] = result["means"]
            second_covariances[state_id] = result["covariance"]
            second_endpoints[state_id] = result["probabilities"]

        # Terminal probabilities: propagate every stage-one terminal mass
        # through the second stage, joining endpoint states by ID. A zero
        # duration second stage is the identity map, so stage-one probabilities
        # survive unchanged.
        probabilities = [0.0] * len(first_states)
        for state_id in first_states:
            terminal = first["probabilities"][index1[state_id]]
            for b, endpoint_id in enumerate(second_states):
                probabilities[index1[endpoint_id]] += (
                    terminal * second_endpoints[state_id][b])

        means = [[0.0] * k for _ in range(2)]
        cross_raw = [[0.0] * k for _ in range(k)]   # E[Y1_i Y2_j] in stage-one order
        second_raw_mixed = [[0.0] * k for _ in range(k)]
        for state_id in first_states:
            mass = first["probabilities"][index1[state_id]]
            cond_means = second_means[state_id]
            cond_cov = second_covariances[state_id]
            for i, metric_id in enumerate(first_metrics):
                y1_mean_cond = first["terminal_means"][index1[state_id]][midx1[metric_id]]
                y2_mean_cond = cond_means[midx2[metric_id]]
                means[0][i] += mass * y1_mean_cond
                means[1][i] += mass * y2_mean_cond
                for j in range(k):
                    other_metric = first_metrics[j]
                    y2_other = cond_means[midx2[other_metric]]
                    cross_raw[i][j] += mass * y1_mean_cond * y2_other
                    second_raw_mixed[i][j] += mass * (
                        cond_cov[midx2[metric_id]][midx2[other_metric]]
                        + y2_mean_cond * y2_other)

        covariance = [[0.0] * (2 * k) for _ in range(2 * k)]
        for i in range(k):
            for j in range(k):
                # Within-stage blocks retain each stage's population covariance.
                covariance[i][j] = first["covariance"][midx1[first_metrics[i]]][midx1[first_metrics[j]]]
                covariance[k + i][k + j] = second_raw_mixed[i][j] - means[1][i] * means[1][j]
                # Law of total covariance: only the between-terminal-state mean
                # variation contributes; conditional cross covariance is zero.
                cross_cov = cross_raw[i][j] - means[0][i] * means[1][j]
                covariance[i][k + j] = cross_cov
                covariance[k + i][j] = cross_raw[j][i] - means[0][j] * means[1][i]

        total_means = [means[0][i] + means[1][i] for i in range(k)]
        total_covariance = [[
            first["covariance"][midx1[first_metrics[i]]][midx1[first_metrics[j]]]
            + (second_raw_mixed[i][j] - means[1][i] * means[1][j])
            + (cross_raw[i][j] - means[0][i] * means[1][j])
            + (cross_raw[j][i] - means[0][j] * means[1][i])
            for j in range(k)] for i in range(k)]

        coordinates = [[1, metric_id] for metric_id in first_metrics]
        coordinates += [[2, metric_id] for metric_id in first_metrics]
        statistics = {
            "states": list(first_states),
            "probabilities": probabilities,
            "metrics": list(first_metrics),
            "coordinates": coordinates,
            "means": means[0] + means[1],
            "covariance": covariance,
            "total_means": total_means,
            "total_covariance": total_covariance,
        }
        for value in probabilities + statistics["means"] + total_means:
            if not math.isfinite(value):
                raise ArithmeticError("nonfinite staged statistic")
        for row in covariance + total_covariance:
            if any(not math.isfinite(value) for value in row):
                raise ArithmeticError("nonfinite staged statistic")
        return statistics

    def run_staged_task(self, task_id, *, fault=None):
        if fault is None:
            def fault(stage):
                return None
        with self.store.transaction() as db:
            task = self.staged_tasks._row(db, task_id)
            if task["status"] == "succeeded":
                return self.staged_results._get(db, task_id)
            plan = json.loads(task["plan"])
            parameters = json.loads(task["parameters"])
            definitions, durations = [], []
            for stage in plan["stages"]:
                _, definition = self.models._resolve(db, stage["model_id"], stage["revision"])
                definitions.append(definition)
                durations.append(stage["duration"])
            statistics = self._joint_statistics(definitions[0], definitions[1],
                                                 parameters, durations)
            fault("after_compute")
            report = {"task_id": task["task_id"], "request_id": task["request_id"],
                      "stages": plan["stages"], "parameters": parameters,
                      "statistics": statistics}
            return self.staged_results._publish(db, task, report, fault)
