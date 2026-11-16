"""Reward SDK 1: uniformization with conditional uniform jump times.

analyze accepts the canonical validated model format documented by the archive.
It owns reward semantics; it does not expose a continuous matrix generator.
"""
import math
from fractions import Fraction

VERSION = "1.0"


def analyze(model, initial, duration):
    states, metrics = model["states"], model["metrics"]
    count, k = len(states), len(metrics)
    index = {s["id"]: i for i, s in enumerate(states)}
    rewards = [[float(Fraction(x)) for x in s["reward"]] for s in states]
    p = [float(Fraction(initial.get(s["id"], 0))) for s in states]
    time = float(duration)
    if not math.isfinite(time) or not 0 <= time <= 8:
        raise ValueError("unsupported duration")
    if time == 0:
        return {"metrics": list(metrics), "states": list(index), "probabilities": p,
                "means": [0.0] * k, "covariance": [[0.0] * k for _ in range(k)]}
    edges = [[] for _ in states]
    totals = [0.0] * count
    for event in model["events"]:
        a, b = index[event["source"]], index[event["target"]]
        rate = float(Fraction(event["rate"]))
        edges[a].append((b, rate, [float(Fraction(x)) for x in event["reward"]]))
        totals[a] += rate
    intensity = max(totals)
    if intensity == 0:
        means = [math.fsum(p[a] * time * rewards[a][i] for a in range(count)) for i in range(k)]
        covariance = [[math.fsum(p[a] * (time * rewards[a][i] - means[i]) *
                                        (time * rewards[a][j] - means[j])
                                    for a in range(count)) for j in range(k)] for i in range(k)]
        return {"metrics": list(metrics), "states": list(index), "probabilities": p,
                "means": means, "covariance": covariance}
    steps = []
    for a in range(count):
        row = [(b, rate / intensity, g) for b, rate, g in edges[a] if rate]
        row.append((a, max(0.0, 1.0 - totals[a] / intensity), [0.0] * k))
        steps.append(row)
    # D is the sum of state reward rates at the n+1 interval states;
    # E is the sum of rewards from the n uniformization ticks.
    d = 2 * k
    first = [[p[a] * x for x in rewards[a] + [0.0] * k] for a in range(count)]
    second = [[[p[a] * x * y for y in rewards[a] + [0.0] * k]
               for x in rewards[a] + [0.0] * k] for a in range(count)]
    residence_square = [[[p[a] * rewards[a][i] * rewards[a][j] for j in range(k)]
                         for i in range(k)] for a in range(count)]
    probabilities = [0.0] * count
    means = [0.0] * k
    raw = [[0.0] * k for _ in range(k)]
    lam = intensity * time
    weight = math.exp(-lam)
    # With lam <=64 this includes the Poisson tail far below binary64 error,
    # including its quadratic reward envelope.
    limit = math.ceil(lam + 16 * math.sqrt(lam + 1) + 80)
    for n in range(limit + 1):
        for a in range(count):
            probabilities[a] += weight * p[a]
            for i in range(k):
                means[i] += weight * (time * first[a][i] / (n + 1) + first[a][k + i])
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
        p, first, second, residence_square = new_p, new_first, new_second, new_square
        weight *= lam / (n + 1)
    covariance = [[raw[i][j] - means[i] * means[j] for j in range(k)] for i in range(k)]
    return {"metrics": list(metrics), "states": list(index), "probabilities": probabilities,
            "means": means, "covariance": covariance}
