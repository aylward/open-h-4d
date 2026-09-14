# SPDX-License-Identifier: Apache-2.0
"""OpenH-4D RFP conformance rules, applied to a ``study.json``.

These are the acquisition-quality thresholds from RFP section 8 (Modality
specifics): resolution and slice spacing for CT, declared cine parameters for
MR, a declared probe source for ultrasound.

Each rule is an **error** by default. A contributor who has a defensible reason
to fall outside a threshold records a waiver in ``study.json.waivers`` with a
justification; that downgrades the rule to a **warning**, so the steering group
sees it rather than the submission being silently rejected or silently accepted.

``min_timepoints`` is the one rule that cannot be waived: OpenH-4D collects 4D
data, and a study with a single time point is a 3D study.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class Rule:
    """One conformance threshold."""

    rule_id: str
    description: str
    applies: Callable[[dict[str, Any]], bool]
    """Whether this rule is relevant to the given study (modality, organ, ...)."""

    check: Callable[[dict[str, Any]], str | None]
    """Returns ``None`` when satisfied, or a message describing the violation."""

    waivable: bool = True


def _geometry(study: dict[str, Any]) -> dict[str, Any]:
    value = study.get("geometry")
    return value if isinstance(value, dict) else {}


def _in_plane_max(study: dict[str, Any]) -> float | None:
    values = _geometry(study).get("in_plane_mm")
    if not isinstance(values, list) or not values:
        return None
    numbers = [v for v in values if isinstance(v, (int, float))]
    return max(numbers) if numbers else None


def _is(modality: str, organ: str | None = None) -> Callable[[dict[str, Any]], bool]:
    def applies(study: dict[str, Any]) -> bool:
        if study.get("modality") != modality:
            return False
        return organ is None or study.get("organ") == organ

    return applies


def _check_in_plane(study: dict[str, Any]) -> str | None:
    worst = _in_plane_max(study)
    if worst is None:
        return "in-plane resolution is not stated"
    if worst > 1.0:
        return f"in-plane resolution is {worst} mm; the RFP requires 1 mm or less for CT"
    return None


def _check_slice_spacing(limit: float, organ: str) -> Callable[[dict[str, Any]], str | None]:
    def check(study: dict[str, Any]) -> str | None:
        spacing = _geometry(study).get("slice_spacing_mm")
        if not isinstance(spacing, (int, float)):
            return "slice spacing is not stated"
        if spacing > limit:
            return (
                f"slice spacing is {spacing} mm; the RFP requires {limit} mm or less for {organ} CT"
            )
        return None

    return check


def _check_low_dose_declared(study: dict[str, Any]) -> str | None:
    ct = study.get("ct")
    if not isinstance(ct, dict) or ct.get("low_dose") is None:
        return "ct.low_dose is not stated; the RFP asks every CT contribution to declare it"
    return None


def _check_mr_cine_declared(study: dict[str, Any]) -> str | None:
    missing = []
    if _in_plane_max(study) is None:
        missing.append("geometry.in_plane_mm")
    if not isinstance(_geometry(study).get("temporal_resolution_ms"), (int, float)):
        missing.append("geometry.temporal_resolution_ms")
    if missing:
        return f"MR cine parameters not stated: {', '.join(missing)}"
    return None


def _check_us_source(study: dict[str, Any]) -> str | None:
    us = study.get("us")
    source = us.get("source") if isinstance(us, dict) else None
    if source not in {"4d_tee", "4d_percutaneous", "other"}:
        return "us.source is not stated; the RFP asks for 4d_tee, 4d_percutaneous, or other"
    return None


def _check_min_timepoints(study: dict[str, Any]) -> str | None:
    n = study.get("n_timepoints")
    if not isinstance(n, int) or n < 2:
        return (
            f"n_timepoints is {n!r}; OpenH-4D collects 4D data, so a study needs at least two "
            f"time points. A single time point is a 3D study."
        )
    return None


RULES: tuple[Rule, ...] = (
    Rule(
        "ct_in_plane_resolution",
        "CT in-plane resolution must be 1 mm or less",
        _is("CT"),
        _check_in_plane,
    ),
    Rule(
        "ct_slice_spacing_cardiac",
        "Cardiac CT slice spacing must be 2 mm or less",
        _is("CT", "heart"),
        _check_slice_spacing(2.0, "cardiac"),
    ),
    Rule(
        "ct_slice_spacing_lung",
        "Lung CT slice spacing must be 3 mm or less",
        _is("CT", "lung"),
        _check_slice_spacing(3.0, "lung"),
    ),
    Rule(
        "ct_low_dose_declared",
        "CT contributions must declare whether the acquisition was low dose",
        _is("CT"),
        _check_low_dose_declared,
    ),
    Rule(
        "mr_cine_resolution_declared",
        "MR contributions must declare cine in-plane and temporal resolution",
        _is("MR"),
        _check_mr_cine_declared,
    ),
    Rule(
        "us_source_declared",
        "Ultrasound contributions must declare the probe source",
        _is("US"),
        _check_us_source,
    ),
    Rule(
        "min_timepoints",
        "Every study must have at least two time points",
        lambda study: True,
        _check_min_timepoints,
        waivable=False,
    ),
)

RULES_BY_ID = {rule.rule_id: rule for rule in RULES}


def waived_rules(study: dict[str, Any]) -> dict[str, str]:
    """Map rule id to justification for every waiver declared in ``study``."""
    waivers = study.get("waivers")
    if not isinstance(waivers, list):
        return {}
    return {
        w["rule"]: w.get("justification", "")
        for w in waivers
        if isinstance(w, dict) and isinstance(w.get("rule"), str)
    }


def check_study(study: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply every applicable rule.

    Returns a list of ``{rule, severity, message, justification?}`` dicts, empty
    when the study conforms. Severity is ``error``, or ``warning`` when a waiver
    covers the rule.
    """
    waivers = waived_rules(study)
    results = []
    for rule in RULES:
        if not rule.applies(study):
            continue
        message = rule.check(study)
        if message is None:
            continue
        waived = rule.waivable and rule.rule_id in waivers
        result: dict[str, Any] = {
            "rule": rule.rule_id,
            "severity": SEVERITY_WARNING if waived else SEVERITY_ERROR,
            "message": message,
        }
        if waived:
            result["justification"] = waivers[rule.rule_id]
        results.append(result)
    return results


def unknown_waivers(study: dict[str, Any]) -> list[str]:
    """Waiver ids that name no real rule -- usually a typo silently doing nothing."""
    return sorted(set(waived_rules(study)) - set(RULES_BY_ID))


def unwaivable_waivers(study: dict[str, Any]) -> list[str]:
    """Waivers naming a rule that cannot be waived, so the contributor is not misled."""
    return sorted(
        rule_id
        for rule_id in waived_rules(study)
        if rule_id in RULES_BY_ID and not RULES_BY_ID[rule_id].waivable
    )
