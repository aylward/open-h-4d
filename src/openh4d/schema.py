# SPDX-License-Identifier: Apache-2.0
"""Validation of the OpenH-4D JSON sidecars.

Two layers:

* **JSON Schema** (``schemas/*.json``) covers structure, types, enums and
  ranges. It is declarative, greppable, and doubles as documentation.
* **Cross-field rules** live here, because JSON Schema cannot express them:
  the HIPAA age cap and its ``age_90_or_older`` escape hatch, and a sweep for
  calendar dates that leaked into a field where they do not belong.

Whole-tree rules -- does ``patient.json`` list the study directories that
actually exist, does ``n_timepoints`` match the file count -- need the
filesystem and live in :mod:`openh4d.verify_layout` instead.

Every function here returns a list of :class:`Finding`; none of them raise on
invalid input. Callers accumulate findings and decide severity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).parent / "schemas"

PATIENT_SCHEMA = "patient"
STUDY_SCHEMA = "study"
MANIFEST_SCHEMA = "manifest"

#: A calendar date is one of the 18 HIPAA Safe Harbor identifiers. Finding one
#: in a sidecar is cheap to check and high value to catch.
DATE_RE = re.compile(r"\b(19|20)\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b")

#: Keys under which a date is expected and therefore allowed.
DATE_ALLOWED_PREFIXES = (
    ("deidentification",),
    ("generated_at",),
    ("source", "converted_at"),
)


@dataclass
class Finding:
    """One validation problem, located precisely enough to act on."""

    code: str
    message: str
    path: str = ""
    """Dotted JSON path to the offending value, e.g. ``demographics.age_years``."""

    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path:
            out["path"] = self.path
        if self.context:
            out["context"] = self.context
        return out


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    """Load and cache a JSON Schema by bare name (``patient``, ``study``, ...)."""
    path = SCHEMA_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(f"no such schema: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name))


def _json_path(parts) -> str:
    """Render a jsonschema error path as a dotted/indexed string."""
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


def validate_against_schema(document: Any, schema_name: str) -> list[Finding]:
    """Structural validation only. Returns one finding per schema violation."""
    findings = []
    for error in sorted(_validator(schema_name).iter_errors(document), key=str):
        findings.append(
            Finding(
                code="E_SCHEMA",
                message=error.message,
                path=_json_path(error.absolute_path),
                context={"schema": schema_name},
            )
        )
    return findings


def check_age_cap(patient: dict[str, Any]) -> list[Finding]:
    """HIPAA Safe Harbor caps reported age at 89.

    Ages of 90 and over must be collapsed into the single ``age_90_or_older``
    flag, because an exact age above 89 is itself an identifier. The schema
    enforces the ``<= 89`` bound; this enforces that exactly one of the two
    representations is used.
    """
    demographics = patient.get("demographics")
    if not isinstance(demographics, dict):
        return []

    age = demographics.get("age_years")
    over_90 = demographics.get("age_90_or_older")

    if over_90 is True and age is not None:
        return [
            Finding(
                code="E_AGE_OVER_89_NOT_SUPPRESSED",
                message=(
                    "age_90_or_older is true, so age_years must be null. An exact age above "
                    "89 is a HIPAA Safe Harbor identifier and cannot be reported."
                ),
                path="demographics.age_years",
                context={"age_years": age},
            )
        ]
    if over_90 is False and age is None:
        return [
            Finding(
                code="E_AGE_MISSING",
                message=(
                    "age_years is null but age_90_or_older is false, so no age is reported "
                    "at all. Set age_years, or set age_90_or_older to true."
                ),
                path="demographics.age_years",
            )
        ]
    return []


def _walk(document: Any, prefix: tuple[str, ...] = ()):
    """Yield ``(path_tuple, value)`` for every leaf in a JSON document."""
    if isinstance(document, dict):
        for key, value in document.items():
            yield from _walk(value, prefix + (str(key),))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from _walk(value, prefix + (str(index),))
    else:
        yield prefix, document


def check_no_leaked_dates(document: Any) -> list[Finding]:
    """Flag calendar dates outside the fields where one is expected.

    A stray ``1962-04-17`` in a free-text field is a date of birth that survived
    de-identification. This catches the common case at near-zero cost; the full
    sweep is :mod:`openh4d.phi_scan`.
    """
    findings = []
    for path, value in _walk(document):
        if not isinstance(value, str):
            continue
        if any(path[: len(allowed)] == allowed for allowed in DATE_ALLOWED_PREFIXES):
            continue
        match = DATE_RE.search(value)
        if match:
            findings.append(
                Finding(
                    code="E_DATE_IN_METADATA",
                    message=(
                        f"found what looks like a calendar date ({match.group(0)!r}). Dates "
                        f"are HIPAA Safe Harbor identifiers -- use an offset relative to the "
                        f"scan (for example days_relative_to_scan) instead of an absolute date."
                    ),
                    path=_json_path(path),
                )
            )
    return findings


def validate_patient(patient: dict[str, Any]) -> list[Finding]:
    """Full document-level validation of a ``patient.json``."""
    findings = validate_against_schema(patient, PATIENT_SCHEMA)
    findings += check_age_cap(patient)
    findings += check_no_leaked_dates(patient)
    return findings


def validate_study(study: dict[str, Any]) -> list[Finding]:
    """Full document-level validation of a ``study.json``.

    Note this does not touch the filesystem: cross-checks between the declared
    geometry and the actual NIfTI headers happen in :mod:`openh4d.verify_layout`.
    """
    findings = validate_against_schema(study, STUDY_SCHEMA)
    findings += check_no_leaked_dates(study)
    findings += _check_timepoint_consistency(study)
    return findings


def _check_timepoint_consistency(study: dict[str, Any]) -> list[Finding]:
    """``n_timepoints`` must equal the declared list, indexed contiguously from 0."""
    findings = []
    timepoints = study.get("timepoints")
    declared = study.get("n_timepoints")
    if not isinstance(timepoints, list) or not isinstance(declared, int):
        return findings

    if len(timepoints) != declared:
        findings.append(
            Finding(
                code="E_TIMEPOINT_COUNT_MISMATCH",
                message=(
                    f"n_timepoints is {declared} but the timepoints list has "
                    f"{len(timepoints)} entries."
                ),
                path="n_timepoints",
            )
        )

    indices = [tp.get("index") for tp in timepoints if isinstance(tp, dict)]
    if indices and indices != list(range(len(indices))):
        findings.append(
            Finding(
                code="E_TIMEPOINT_INDEX_NOT_CONTIGUOUS",
                message=(
                    f"timepoint indices must run contiguously from 0; got {indices}. A gap "
                    f"usually means a time point was lost in transfer."
                ),
                path="timepoints",
            )
        )
    return findings


def validate_manifest(manifest: dict[str, Any]) -> list[Finding]:
    """Full document-level validation of a ``manifest.json``."""
    return validate_against_schema(manifest, MANIFEST_SCHEMA)
