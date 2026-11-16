"""Continuous-path moments for one stage of a fixed two-stage plan.

This engine is the analysis_archive's own continuous calculation. It uses the
same uniformization / conditional-uniform-jump-time construction as
vendor.sdk1.analyze, so its means, covariance and terminal probabilities share
SDK1's numerical semantics (self events, parallel events, shared vector
payments, negative rewards, random initial states). In addition it returns the
terminal-state-conditioned first moments E[Y_i | X(T)=b], which are required to
join the two stages along one continuous random path without assuming
independence between the first-stage cumulative amount and the boundary state.

The SDK1 first-moment accumulator only carries the residence piece at the
current uniformization state, so it cannot produce a terminal-conditioned
cumulative moment. The extra joint accumulator below keeps the origin state's
residence rate (rewards[a], not rewards[b]) across each tick and adds the event
payment at the jump; that is the standard Markov-reward-chain construction
E[(residence integral + event payments) * 1{X(T)=b}].

The engine never reads or writes storage; it consumes the canonical validated
model format. All outputs are binary64 floats.
"""
import math
from fractions import Fraction


def stage_moments(model, initial, duration):
    """Return probabilities, means, covariance and terminal-conditioned means.

    Inputs follow vendor.sdk1.analyze: a canonical model, a mapping of state
    IDs to probabilities (omitted states default to zero) and a finite
    duration in [0, 8]. The returned keys are:

    * states, probabilities: terminal distribution in the model's state order
    * means, covariance: first and population second-central moments in the
      model's metric order
    * terminal_means: one row per terminal state, each holding the conditional
      expectation E[Y_i | X(T)=state] for every metric i
    """
    states, metrics = model["states"], model["metrics"]
    count, k = len(states), len(metrics)
    index = {s["id"]: i for i, s in enumerate(states)}
    rewards = [[float(Fraction(x)) for x in s["reward"]] for s in states]
    p = [float(Fraction(initial.get(s["id"], 0))) for s in states]
    time = float(duration)
    if not math.isfinite(time) or not 0 <= time <= 8:
        raise ValueError("unsupported duration")
    state_ids = list(index)
    if time == 0:
        return {"states": state_ids, "probabilities": list(p),
                "means": [0.0] * k, "covariance": [[0.0] * k for _ in range(k)],
                "terminal_means": [[0.0] * k for _ in range(count)]}
    edges = [[] for _ in states]
    totals = [0.0] * count
    for event in model["events"]:
        a, b = index[event["source"]], index[event["target"]]
        rate = float(Fraction(event["rate"]))
        edges[a].append((b, rate, [float(Fraction(x)) for x in event["reward"]]))
        totals[a] += rate
    intensity = max(totals)
    if intensity == 0:
        # No events: the path keeps its initial state and only accumulates the
        # residence integral, so conditioning on the terminal state fixes Y.
        means = [math.fsum(p[a] * time * rewards[a][i] for a in range(count)) for i in range(k)]
        covariance = [[math.fsum(p[a] * (time * rewards[a][i] - means[i]) *
                                        (time * rewards[a][j] - means[j])
                                    for a in range(count)) for j in range(k)] for i in range(k)]
        terminal_means = [[time * rewards[a][i] for i in range(k)] for a in range(count)]
        return {"states": state_ids, "probabilities": list(p), "means": means,
                "covariance": covariance, "terminal_means": terminal_means}
    steps = []
    for a in range(count):
        row = [(b, rate / intensity, g) for b, rate, g in edges[a] if rate]
        row.append((a, max(0.0, 1.0 - totals[a] / intensity), [0.0] * k))
        steps.append(row)
    d = 2 * k
    first = [[p[a] * x for x in rewards[a] + [0.0] * k] for a in range(count)]
    second = [[[p[a] * x * y for y in rewards[a] + [0.0] * k]
               for x in rewards[a] + [0.0] * k] for a in range(count)]
    residence_square = [[[p[a] * rewards[a][i] * rewards[a][j] for j in range(k)]
                         for i in range(k)] for a in range(count)]
    # Joint cumulative first moments with the terminal state indicator, split
    # by reward kind because only the residence integral scales by the
    # conditional uniform interval length time/(n+1):
    #   joint_res[a][i] = E[R_i * 1{X(T)=a}] per single uniformization interval
    #   joint_evt[a][i] = E[G_i * 1{X(T)=a}] over the jump payments
    # where R is a per-interval residence rate and G is the event total.
    joint_res = [[p[a] * rewards[a][i] for i in range(k)] for a in range(count)]
    joint_evt = [[0.0] * k for _ in range(count)]
    probabilities = [0.0] * count
    terminal_residence = [[0.0] * k for _ in range(count)]
    terminal_event = [[0.0] * k for _ in range(count)]
    means = [0.0] * k
    raw = [[0.0] * k for _ in range(k)]
    lam = intensity * time
    weight = math.exp(-lam)
    limit = math.ceil(lam + 16 * math.sqrt(lam + 1) + 80)
    for n in range(limit + 1):
        for a in range(count):
            probabilities[a] += weight * p[a]
            for i in range(k):
                means[i] += weight * (time * first[a][i] / (n + 1) + first[a][k + i])
                terminal_residence[a][i] += weight * time / (n + 1) * joint_res[a][i]
                terminal_event[a][i] += weight * joint_evt[a][i]
                for j in range(k):
                    dwell = time * time * (second[a][i][j] + residence_square[a][i][j]) / ((n + 1) * (n + 2))
                    mixed = time * (second[a][i][k + j] + second[a][k + i][j]) / (n + 1)
                    raw[i][j] += weight * (dwell + mixed + second[a][k + i][k + j])
        if n == limit:
            break
        new_p = [0.0] * count
        new_first = [[0.0] * d for _ in states]
        new_second = [[[0.0] * d for _ in range(d)] for _ in states]
        new_square = [[[0.0] * k for _ in range(k)] for _ in states]
        new_joint_res = [[0.0] * k for _ in states]
        new_joint_evt = [[0.0] * k for _ in states]
        for a in range(count):
            for b, chance, event_reward in steps[a]:
                if chance == 0:
                    continue
                delta = rewards[b] + event_reward
                new_p[b] += chance * p[a]
                for i in range(d):
                    new_first[b][i] += chance * (first[a][i] + delta[i] * p[a])
                    for j in range(d):
                        new_second[b][i][j] += chance * (
                            second[a][i][j] + delta[i] * first[a][j] +
                            delta[j] * first[a][i] + delta[i] * delta[j] * p[a])
                for i in range(k):
                    for j in range(k):
                        new_square[b][i][j] += chance * (
                            residence_square[a][i][j] + rewards[b][i] * rewards[b][j] * p[a])
                    # Per-interval residence accumulator: the new interval is
                    # spent in the destination state b.
                    new_joint_res[b][i] += chance * (joint_res[a][i] + rewards[b][i] * p[a])
                    # Event accumulator: the jump a -> b pays its vector once.
                    new_joint_evt[b][i] += chance * (joint_evt[a][i] + event_reward[i] * p[a])
        p, first, second, residence_square = new_p, new_first, new_second, new_square
        joint_res, joint_evt = new_joint_res, new_joint_evt
        weight *= lam / (n + 1)
    covariance = [[raw[i][j] - means[i] * means[j] for j in range(k)] for i in range(k)]
    terminal_means = [[(terminal_residence[a][i] + terminal_event[a][i]) / probabilities[a]
                       if probabilities[a] > 0.0 else 0.0
                       for i in range(k)] for a in range(count)]
    return {"states": state_ids, "probabilities": probabilities, "means": means,
            "covariance": covariance, "terminal_means": terminal_means}
