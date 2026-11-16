import json
import math
from fractions import Fraction
from .errors import ValidationError


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValidationError("identity must be a nonempty string of at most 64 characters")
    return value


def rational(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Fraction)):
        raise ValidationError("expected an integer, Fraction or rational string")
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValidationError("invalid rational") from exc
    if result.denominator > 1000000 or abs(result) > 8:
        raise ValidationError("rational outside supported range")
    return result


def vector(values, size):
    if not isinstance(values, (list, tuple)) or len(values) != size:
        raise ValidationError("reward vector length")
    return [str(rational(x)) for x in values]


def duration(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Fraction)):
        raise ValidationError("invalid duration")
    try:
        result = float(Fraction(value) if isinstance(value, str) else value)
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        raise ValidationError("invalid duration") from exc
    if not math.isfinite(result) or not 0 <= result <= 8:
        raise ValidationError("duration must be finite and in [0, 8]")
    return result


def model(value):
    try:
        metrics = list(value["metrics"])
        if not 1 <= len(metrics) <= 3:
            raise ValidationError("one to three metrics required")
        metrics = [identifier(x) for x in metrics]
        if len(set(metrics)) != len(metrics):
            raise ValidationError("duplicate metric")
        states = [{"id": identifier(s["id"]), "reward": vector(s["reward"], len(metrics))}
                  for s in value["states"]]
        ids = [s["id"] for s in states]
        if not 1 <= len(ids) <= 6 or len(set(ids)) != len(ids):
            raise ValidationError("one to six distinct states required")
        events = []
        totals = {s: Fraction(0) for s in ids}
        for e in value["events"]:
            source, target = identifier(e["source"]), identifier(e["target"])
            if source not in totals or target not in totals:
                raise ValidationError("unknown event endpoint")
            rate = rational(e["rate"])
            if rate < 0:
                raise ValidationError("negative rate")
            totals[source] += rate
            events.append({"id": identifier(e["id"]), "source": source, "target": target,
                           "rate": str(rate), "reward": vector(e["reward"], len(metrics))})
        if len(events) > 18 or len({e["id"] for e in events}) != len(events):
            raise ValidationError("too many events or duplicate event identity")
        if any(x > 8 for x in totals.values()):
            raise ValidationError("total event intensity exceeds 8")
        return {"metrics": metrics, "states": states, "events": events}
    except (KeyError, TypeError) as exc:
        raise ValidationError("malformed model") from exc


def cohort(states, initial, time):
    try:
        states = [identifier(x) for x in states]
        if not 1 <= len(states) <= 6 or len(set(states)) != len(states):
            raise ValidationError("invalid state directory")
        if not isinstance(initial, dict) or set(initial) - set(states):
            raise ValidationError("unknown initial state")
        probabilities = {s: rational(initial.get(s, 0)) for s in states}
        if any(p < 0 for p in probabilities.values()) or sum(probabilities.values()) != 1:
            raise ValidationError("initial probabilities must be nonnegative and sum exactly to one")
        return {"states": states, "initial": {s: str(probabilities[s]) for s in states},
                "duration": duration(time)}
    except TypeError as exc:
        raise ValidationError("malformed cohort") from exc


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
