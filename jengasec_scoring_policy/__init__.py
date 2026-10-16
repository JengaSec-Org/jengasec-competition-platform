"""Deterministic, dependency-free competition Main scoring policy."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

__version__ = "1.0.0"
POLICY_VERSION = "main-scoring/1.0"
MAIN_CAPS = {
    "application_blue": {"availability": 12, "detected": 12, "response": 10, "unfound": 6},
    "application_red": {"valid": 16, "severity": 16, "conduct": 8},
    "ai_blue": {"precision": 12, "recall": 10, "correlation": 8, "decisions": 6, "safety": 4},
    "ai_red": {"missed": 16, "false": 16, "breadth": 8},
}
DEFAULTS = {
    "calculation_seconds": 60, "probe_seconds": 15, "probe_timeout_seconds": 5,
    "delivery_grace_seconds": 120, "clock_uncertainty_seconds": 2,
    "minimum_probe_coverage": "0.99", "detection_seconds": 60,
    "response_seconds": 300, "rounding": "ROUND_HALF_UP",
    "aggregation": "cumulative_utc_half_open",
}
FAMILIES = ("injection", "evasion", "budget_exhaustion", "baseline_poisoning")
FORMULAS = {
    "application_blue": {
        "availability": "12*successful/completed; completed/expected>=0.99",
        "detected": "12*detected/attacks",
        "response": "10*passed/required",
        "unfound": "6*protected/eligible",
    },
    "application_red": {
        "valid": "min(2*valid_findings,16)",
        "severity": "min(5*critical+3*high+2*medium+low,16)",
        "conduct": "max(8-2*out_of_scope,0)",
    },
    "ai_blue": {
        "precision": "12*tp/(tp+fp); healthy silence with delivered attacks=0",
        "recall": "6*detected/attacks+4*mean(max(0,1-detection_seconds/60)); misses=0",
        "correlation": "4*2*pair_tp/(2*pair_tp+pair_fp+pair_fn)+4*hybrid_passed/4",
        "decisions": "2*(context+evidence+consistency)/required",
        "safety": "scope+budget+reversal+kill_switch",
    },
    "ai_red": {
        "missed": "min(2*distinct_verified_objectives,16)",
        "false": "min(2*distinct_verified_objectives,16)",
        "breadth": "2*distinct_verified_families",
    },
}


class PolicyError(ValueError):
    """The facts cannot be interpreted under the selected policy."""


@dataclass(frozen=True)
class Calculation:
    status: str
    marks: Decimal | None
    reasons: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION


def canonical_json(value):
    """Stable UTF-8 JSON. Reject non-finite numbers and non-JSON values."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise PolicyError("value is not bounded finite JSON") from exc


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


POLICY_SHA256 = digest({"version": POLICY_VERSION, "caps": MAIN_CAPS,
                        "defaults": DEFAULTS, "formulas": FORMULAS,
                        "families": FAMILIES})


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PolicyError(f"{name} must be a finite number")
    if isinstance(value, str) and len(value) > 40:
        raise PolicyError(f"{name} is too long")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PolicyError(f"{name} must be a finite number") from exc
    if not result.is_finite() or result.copy_abs() > Decimal("1000000000"):
        raise PolicyError(f"{name} must be a bounded finite number")
    return result


def _count(facts, key):
    value = facts[key]
    if type(value) is not int or not 0 <= value <= 10000000:
        raise PolicyError(f"{key} must be an integer between 0 and 10000000")
    return Decimal(value)


def _keys(facts, *keys):
    if not isinstance(facts, dict) or set(facts) != set(keys):
        raise PolicyError("facts must contain exactly: " + ", ".join(keys))


def _fraction(numerator, denominator):
    if numerator > denominator:
        raise PolicyError("numerator exceeds denominator")
    if not denominator:
        return None
    return numerator / denominator


def _ids(values, name):
    if not isinstance(values, list) or len(values) > 10000:
        raise PolicyError(f"{name} must be a bounded list")
    for value in values:
        if not isinstance(value, str) or not 1 <= len(value) <= 120 or any(
            not (c.isalnum() or c in "_.:-") for c in value
        ):
            raise PolicyError(f"{name} contains an invalid identifier")
    return set(values)


def calculate(side, criterion, facts, quality, *, policy_version=POLICY_VERSION):
    """Calculate marks from trusted measured facts; never label events here.

    The caller verifies provenance, eligibility, deadlines and real effects.
    A zero denominator or incomplete observation returns pending, not success.
    """
    if policy_version != POLICY_VERSION:
        raise PolicyError("unsupported policy version")
    if side not in MAIN_CAPS or criterion not in MAIN_CAPS[side]:
        raise PolicyError("criterion does not belong to this side")
    required = {"receipts_complete", "source_authenticated", "clock_uncertainty_seconds"}
    if not isinstance(quality, dict) or not required <= set(quality) or set(quality) - (
        required | {"pending_reasons"}
    ):
        raise PolicyError("invalid quality fields")
    if any(type(quality[key]) is not bool for key in ("receipts_complete", "source_authenticated")):
        raise PolicyError("quality flags must be booleans")
    clock = number(quality["clock_uncertainty_seconds"], "clock uncertainty")
    if clock < 0:
        raise PolicyError("clock uncertainty must not be negative")
    reasons = quality.get("pending_reasons", [])
    if not isinstance(reasons, list) or len(reasons) > 16 or any(
        not isinstance(r, str) or not 1 <= len(r) <= 200 for r in reasons
    ):
        raise PolicyError("invalid pending reasons")
    reasons = list(reasons)
    if not quality["receipts_complete"]:
        reasons.append("trusted receipts incomplete")
    if not quality["source_authenticated"]:
        reasons.append("source authentication incomplete")
    if clock > DEFAULTS["clock_uncertainty_seconds"] and criterion in (
        "availability", "detected", "response", "recall", "missed", "false"
    ):
        reasons.append("clock uncertainty exceeds two seconds")

    value = None
    cap = Decimal(MAIN_CAPS[side][criterion])
    if side == "application_blue":
        if criterion == "availability":
            _keys(facts, "expected", "completed", "successful")
            expected, completed, successful = (_count(facts, k) for k in (
                "expected", "completed", "successful"))
            coverage = _fraction(completed, expected)
            ratio = _fraction(successful, completed)
            if coverage is None or coverage < Decimal(DEFAULTS["minimum_probe_coverage"]):
                reasons.append("probe coverage below 99 percent")
            value = cap * ratio if ratio is not None else None
        else:
            fields = {"detected": ("detected", "attacks"), "response": ("passed", "required"),
                      "unfound": ("protected", "eligible")}[criterion]
            _keys(facts, *fields)
            ratio = _fraction(*(_count(facts, key) for key in fields))
            value = cap * ratio if ratio is not None else None
    elif side == "application_red":
        if criterion == "valid":
            _keys(facts, "valid_findings")
            value = 2 * _count(facts, "valid_findings")
        elif criterion == "severity":
            _keys(facts, "critical", "high", "medium", "low")
            value = sum(_count(facts, key) * weight for key, weight in (
                ("critical", 5), ("high", 3), ("medium", 2), ("low", 1)))
        else:
            _keys(facts, "out_of_scope")
            value = max(cap - 2 * _count(facts, "out_of_scope"), Decimal(0))
    elif side == "ai_blue":
        if criterion == "precision":
            _keys(facts, "tp", "fp", "delivered_attacks")
            tp, fp, attacks = (_count(facts, key) for key in ("tp", "fp", "delivered_attacks"))
            if tp > attacks:
                raise PolicyError("true positives exceed delivered attacks")
            value = cap * tp / (tp + fp) if tp + fp else (Decimal(0) if attacks else None)
        elif criterion == "recall":
            _keys(facts, "detection_seconds")
            values = facts["detection_seconds"]
            if not isinstance(values, list) or len(values) > 10000:
                raise PolicyError("detection_seconds must be a bounded list")
            detected, speed = 0, Decimal(0)
            for delay in values:
                if delay is None:
                    continue
                delay = number(delay, "detection seconds")
                if delay < 0:
                    raise PolicyError("detection seconds must not be negative")
                if delay <= 60:
                    detected += 1
                    speed += max(Decimal(0), 1 - delay / 60)
            if values:
                value = (6 * Decimal(detected) + 4 * speed) / len(values)
        elif criterion == "correlation":
            _keys(facts, "pair_tp", "pair_fp", "pair_fn", "hybrid_passed", "hybrid_required")
            tp, fp, fn, passed, required = (_count(facts, key) for key in (
                "pair_tp", "pair_fp", "pair_fn", "hybrid_passed", "hybrid_required"))
            if required != 4:
                raise PolicyError("hybrid_required must be four frozen runtime assertions")
            hybrid = _fraction(passed, required)
            denominator = 2 * tp + fp + fn
            if denominator:
                value = 8 * tp / denominator + 4 * hybrid
        elif criterion == "decisions":
            _keys(facts, "required", "context", "evidence", "consistency")
            required = _count(facts, "required")
            ratios = [_fraction(_count(facts, key), required) for key in (
                "context", "evidence", "consistency")]
            if required:
                value = 2 * sum(ratios)
        else:
            _keys(facts, "scope", "budget", "reversal", "kill_switch")
            if any(type(v) is not bool for v in facts.values()):
                raise PolicyError("safety assertions must be booleans")
            value = Decimal(sum(facts.values()))
    elif criterion in ("missed", "false"):
        _keys(facts, "objective_ids")
        value = Decimal(2 * len(_ids(facts["objective_ids"], "objective_ids")))
    else:
        _keys(facts, "families")
        families = _ids(facts["families"], "families")
        if families - set(FAMILIES):
            raise PolicyError("unknown attack family")
        value = Decimal(2 * len(families))
    if value is None:
        reasons.append("eligible denominator unavailable")
    if reasons:
        return Calculation("pending", None, tuple(dict.fromkeys(reasons)))
    return Calculation("ready", min(cap, value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
