# SPDX-License-Identifier: Apache-2.0
"""The authoritative OpenH-4D submission layout checker.

Everything else defers to this module. The verify skill runs it, the submission
evaluator treats ``compliant: true`` as the single pass criterion for its layout
dimension, and the convert skill runs it on one study before scaling to the
rest. There is exactly one implementation of "is this a valid submission".

The normative prose spec is ``skills/openh-4d-shared/layout-spec.md``;
``tests/test_spec_consistency.py`` asserts the two do not drift apart.

Usage::

    python -m openh4d.verify_layout <submission-root>
    python -m openh4d.verify_layout <submission-root> --study STAN-0001A

Prints a machine-readable JSON report on stdout. Exit 0 = compliant,
1 = not compliant. Warnings never affect the exit code.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import conformance, naming
from .check_volumes import (
    SPACING_TOLERANCE_MM,
    VolumeHeader,
    VolumeReadError,
    compare_geometry,
    read_header,
)
from .naming import KIND_EHR, KIND_STUDY, InvalidNameError

SCHEMA_VERSION = "openh-4d/verify/1.0"

README_NAME = "README.md"
LICENSE_NAME = "LICENSE"
MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "checksums.sha256"
STUDY_JSON = "study.json"
PATIENT_JSON = "patient.json"
SINGLE_4D_NAME = "image4d.nii.gz"

ALLOWED_ROOT_FILES = {README_NAME, LICENSE_NAME, MANIFEST_NAME, CHECKSUMS_NAME}

TIMEPOINT_FILE_RE = re.compile(r"^t(\d{4})\.nii\.gz$")
TIMEPOINT_DIR_RE = re.compile(r"^t(\d{4})$")

#: Names that suggest a re-identification key or free PHI. Matched on word-ish
#: boundaries so ordinary words containing these letters (``graphics`` contains
#: ``phi``) do not trip the check.
FORBIDDEN_NAME_RE = re.compile(
    r"(?:^|[^a-z0-9])(crosswalk|mrn|phi|linking|deidentification-key)(?:[^a-z0-9]|$)"
)

LAYOUT_TIMEPOINT_VOLUMES = "timepoint_volumes"
LAYOUT_DICOM_PHASES = "dicom_phases"
LAYOUT_SINGLE_4D = "single_4d"


@dataclass
class Report:
    """Accumulates findings for one verification run."""

    root: Path
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def error(self, code: str, message: str, path: str = "") -> None:
        self.errors.append(_finding(code, message, path))

    def warn(self, code: str, message: str, path: str = "") -> None:
        self.warnings.append(_finding(code, message, path))

    @property
    def compliant(self) -> bool:
        return not self.errors

    def as_dict(self, tool_version: str) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "schema": SCHEMA_VERSION,
            "tool_version": tool_version,
            "compliant": self.compliant,
            "summary": self.summary,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _finding(code: str, message: str, path: str) -> dict[str, Any]:
    out = {"code": code, "message": message}
    if path:
        out["path"] = path
    return out


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(document, error_message)``; exactly one is None."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"not valid JSON: {exc}"
    except OSError as exc:
        return None, f"could not be read: {exc}"
    if not isinstance(document, dict):
        return None, f"must contain a JSON object, got {type(document).__name__}"
    return document, None


# --- root ---------------------------------------------------------------------


def _check_root_files(root: Path, report: Report) -> None:
    """README.md, LICENSE and manifest.json live only at the submission root."""
    for required, why in (
        (README_NAME, "the data card; Hugging Face renders it as the dataset landing page"),
        (LICENSE_NAME, "the CC BY 4.0 licence text OpenH-4D requires for the data"),
        (MANIFEST_NAME, "the generated index; run 'python -m openh4d.manifest <root>'"),
    ):
        if not (root / required).is_file():
            report.error(
                f"E_MISSING_{required.split('.')[0].upper()}",
                f"{required} is missing from the submission root -- {why}",
                required,
            )

    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.name not in ALLOWED_ROOT_FILES:
            report.warn(
                "W_UNEXPECTED_ROOT_ENTRY",
                f"{entry.name} is not part of the OpenH-4D layout. Expected only "
                f"{', '.join(sorted(ALLOWED_ROOT_FILES))} plus study and _ehr directories.",
                _rel(root, entry),
            )


def _check_forbidden_names(root: Path, report: Report) -> None:
    """A re-identification crosswalk must never travel with the data.

    The crosswalk is written outside the submission root by design, but a
    contributor can still copy one in by hand, and that is the single worst
    thing that could ship in an OpenH-4D submission.
    """
    for path in root.rglob("*"):
        match = FORBIDDEN_NAME_RE.search(path.name.lower())
        if match:
            report.error(
                "E_FORBIDDEN_FILE",
                f"{path.name!r} matches {match.group(1)!r}, which suggests a re-identification "
                f"key or unredacted PHI. Nothing linking OpenH-4D identifiers back to source "
                f"patient identifiers may appear inside a submission.",
                _rel(root, path),
            )


def _check_case_collisions(names: list[str], root: Path, report: Report) -> None:
    """Two names differing only in case break on Windows and macOS."""
    seen: dict[str, str] = {}
    for name in names:
        lowered = name.lower()
        if lowered in seen and seen[lowered] != name:
            report.error(
                "E_CASE_COLLISION",
                f"{name!r} and {seen[lowered]!r} differ only in case. They would collide on "
                f"the case-insensitive filesystems used by Windows and macOS.",
                name,
            )
        seen.setdefault(lowered, name)


# --- study --------------------------------------------------------------------


@dataclass
class StudyLayout:
    """Which of the three image forms a study directory actually uses."""

    timepoint_files: list[Path] = field(default_factory=list)
    timepoint_indices: list[int] = field(default_factory=list)
    dicom_dirs: list[Path] = field(default_factory=list)
    dicom_indices: list[int] = field(default_factory=list)
    dicom_root: Path | None = None
    dicom_loose_files: list[Path] = field(default_factory=list)
    single_4d: Path | None = None

    @property
    def has_form_a(self) -> bool:
        return bool(self.timepoint_files)

    @property
    def has_form_b(self) -> bool:
        return self.dicom_root is not None

    @property
    def has_form_c(self) -> bool:
        return self.single_4d is not None


def _inspect_study_dir(study_dir: Path) -> StudyLayout:
    layout = StudyLayout()

    for entry in sorted(study_dir.iterdir()):
        if entry.is_file():
            match = TIMEPOINT_FILE_RE.match(entry.name)
            if match:
                layout.timepoint_files.append(entry)
                layout.timepoint_indices.append(int(match.group(1)))
            elif entry.name == SINGLE_4D_NAME:
                layout.single_4d = entry
        elif entry.is_dir() and entry.name == "dicom":
            layout.dicom_root = entry
            for sub in sorted(entry.iterdir()):
                match = TIMEPOINT_DIR_RE.match(sub.name)
                if sub.is_dir() and match:
                    layout.dicom_dirs.append(sub)
                    layout.dicom_indices.append(int(match.group(1)))
                elif sub.is_file():
                    layout.dicom_loose_files.append(sub)

    return layout


def _resolve_form(layout: StudyLayout, study: dict[str, Any] | None) -> str | None:
    """Which declared ``file_layout`` the on-disk contents correspond to."""
    derived = bool(study and study.get("derived_from_dicom"))
    if layout.has_form_b and layout.has_form_a and derived:
        return LAYOUT_DICOM_PHASES
    if layout.has_form_b and not layout.has_form_a and not layout.has_form_c:
        return LAYOUT_DICOM_PHASES
    if layout.has_form_a and not layout.has_form_b and not layout.has_form_c:
        return LAYOUT_TIMEPOINT_VOLUMES
    if layout.has_form_c and not layout.has_form_a and not layout.has_form_b:
        return LAYOUT_SINGLE_4D
    return None


def _check_contiguous(indices: list[int], what: str, study_rel: str, report: Report) -> None:
    expected = list(range(len(indices)))
    if sorted(indices) != expected:
        missing = sorted(set(range(max(indices) + 1)) - set(indices)) if indices else []
        detail = f" Missing index/indices {missing}." if missing else ""
        report.error(
            "E_TIMEPOINT_GAP",
            f"{what} must be numbered contiguously from 0000. Found {sorted(indices)}."
            f"{detail} A gap almost always means a time point was lost in transfer.",
            study_rel,
        )


def _verify_study(
    study_dir: Path, root: Path, report: Report, expect_patient_id: str, expect_study_id: str
) -> dict[str, Any] | None:
    """Verify one study directory. Returns a summary dict, or None if unusable."""
    study_rel = _rel(root, study_dir)
    study_json_path = study_dir / STUDY_JSON

    study: dict[str, Any] | None = None
    if not study_json_path.is_file():
        report.error(
            "E_MISSING_STUDY_JSON",
            f"{STUDY_JSON} is missing. Every study directory needs one -- copy "
            f"skills/openh-4d-shared/study-json-template.json and fill it in.",
            f"{study_rel}/{STUDY_JSON}",
        )
    else:
        study, problem = _load_json(study_json_path)
        if problem:
            report.error(
                "E_STUDY_JSON_UNREADABLE", f"{STUDY_JSON} {problem}", f"{study_rel}/{STUDY_JSON}"
            )

    if study is not None:
        from .schema import validate_study

        for finding in validate_study(study):
            report.error(
                finding.code,
                f"{STUDY_JSON}: {finding.message}"
                + (f" (at {finding.path})" if finding.path else ""),
                f"{study_rel}/{STUDY_JSON}",
            )

        if study.get("study_id") != expect_study_id:
            report.error(
                "E_STUDY_ID_MISMATCH",
                f"{STUDY_JSON} declares study_id {study.get('study_id')!r} but the directory "
                f"is named {expect_study_id!r}.",
                f"{study_rel}/{STUDY_JSON}",
            )
        if study.get("patient_id") != expect_patient_id:
            report.error(
                "E_PATIENT_ID_MISMATCH",
                f"{STUDY_JSON} declares patient_id {study.get('patient_id')!r} but the "
                f"directory name implies {expect_patient_id!r}.",
                f"{study_rel}/{STUDY_JSON}",
            )

        _check_waivers(study, study_rel, report)
        for result in conformance.check_study(study):
            code = (
                "E_CONFORMANCE"
                if result["severity"] == conformance.SEVERITY_ERROR
                else "W_CONFORMANCE"
            )
            message = f"{result['rule']}: {result['message']}"
            if result["severity"] == conformance.SEVERITY_ERROR:
                report.error(code, message, f"{study_rel}/{STUDY_JSON}")
            else:
                report.warn(
                    code,
                    f"{message} (waived: {result.get('justification', '')})",
                    f"{study_rel}/{STUDY_JSON}",
                )

    layout = _inspect_study_dir(study_dir)
    found_form = _resolve_form(layout, study)

    if not (layout.has_form_a or layout.has_form_b or layout.has_form_c):
        report.error(
            "E_NO_IMAGE_DATA",
            "no image data found. Expected per-time-point volumes (t0000.nii.gz ...), a "
            "dicom/ directory with per-phase subdirectories, or a single image4d.nii.gz.",
            study_rel,
        )
        return None

    if found_form is None:
        present = [
            name
            for name, has in (
                ("per-time-point NIfTI", layout.has_form_a),
                ("dicom/", layout.has_form_b),
                (SINGLE_4D_NAME, layout.has_form_c),
            )
            if has
        ]
        report.error(
            "E_MIXED_LAYOUT",
            f"a study directory must use exactly one image form, but found {', '.join(present)}. "
            f"The one legal combination is a retained dicom/ tree alongside converted "
            f"per-time-point NIfTI, which requires file_layout 'dicom_phases' and "
            f"derived_from_dicom true in {STUDY_JSON}.",
            study_rel,
        )
        return None

    declared_form = study.get("file_layout") if study else None
    if study is not None and declared_form != found_form:
        report.error(
            "E_LAYOUT_MISMATCH",
            f"{STUDY_JSON} declares file_layout {declared_form!r} but the directory contains "
            f"{found_form!r}.",
            f"{study_rel}/{STUDY_JSON}",
        )

    if found_form == LAYOUT_SINGLE_4D:
        report.warn(
            "W_FALLBACK_LAYOUT",
            f"{SINGLE_4D_NAME} is the accepted fallback form. The canonical forms are "
            f"per-time-point volumes (t0000.nii.gz ...) or a dicom/ directory with per-phase "
            f"subdirectories -- both are easier to stream, spot-check, and repair one phase of.",
            study_rel,
        )

    multiframe = bool(study and study.get("multiframe"))
    n_found = _count_and_check_timepoints(layout, found_form, multiframe, study_rel, report)

    if study is not None and n_found is not None:
        declared = study.get("n_timepoints")
        if isinstance(declared, int) and declared != n_found:
            report.error(
                "E_TIMEPOINT_COUNT_MISMATCH",
                f"{STUDY_JSON} declares n_timepoints {declared} but {n_found} time point(s) "
                f"are present on disk.",
                study_rel,
            )

    headers = _check_volume_geometry(layout, found_form, study, study_rel, report)
    _check_declared_derived(study_dir, study, study_rel, report)

    return {
        "study_id": expect_study_id,
        "patient_id": expect_patient_id,
        "file_layout": found_form,
        "n_timepoints": n_found,
        "modality": (study or {}).get("modality"),
        "organ": (study or {}).get("organ"),
        "bytes": _dir_size(study_dir),
        "n_volumes_read": len(headers),
    }


def _check_waivers(study: dict[str, Any], study_rel: str, report: Report) -> None:
    for rule_id in conformance.unknown_waivers(study):
        report.error(
            "E_UNKNOWN_WAIVER",
            f"{STUDY_JSON} waives {rule_id!r}, which is not a conformance rule. Known rules: "
            f"{', '.join(sorted(conformance.RULES_BY_ID))}. A typo here silently does nothing.",
            f"{study_rel}/{STUDY_JSON}",
        )
    for rule_id in conformance.unwaivable_waivers(study):
        report.error(
            "E_UNWAIVABLE_WAIVER",
            f"{STUDY_JSON} waives {rule_id!r}, which cannot be waived.",
            f"{study_rel}/{STUDY_JSON}",
        )


def _count_and_check_timepoints(
    layout: StudyLayout, form: str, multiframe: bool, study_rel: str, report: Report
) -> int | None:
    if form == LAYOUT_TIMEPOINT_VOLUMES or (form == LAYOUT_DICOM_PHASES and layout.has_form_a):
        _check_contiguous(layout.timepoint_indices, "per-time-point volumes", study_rel, report)
        return len(layout.timepoint_indices)

    if form == LAYOUT_DICOM_PHASES:
        if multiframe:
            if layout.dicom_dirs:
                report.error(
                    "E_MULTIFRAME_WITH_PHASE_DIRS",
                    f"{STUDY_JSON} sets multiframe true, but dicom/ has per-phase "
                    f"subdirectories. An enhanced/multi-frame container already holds every "
                    f"time point, so its files go directly in dicom/.",
                    study_rel,
                )
            if not layout.dicom_loose_files:
                report.error(
                    "E_NO_IMAGE_DATA",
                    "multiframe is true but dicom/ contains no files.",
                    study_rel,
                )
            return None  # the declared count is authoritative for a container
        if not layout.dicom_dirs:
            report.error(
                "E_NO_IMAGE_DATA",
                "dicom/ contains no per-phase subdirectories (t0000/, t0001/, ...). If this "
                f"is enhanced/multi-frame DICOM, set multiframe true in {STUDY_JSON} and put "
                f"the container files directly in dicom/.",
                study_rel,
            )
            return None
        _check_contiguous(layout.dicom_indices, "per-phase DICOM directories", study_rel, report)
        for phase_dir in layout.dicom_dirs:
            if not any(p.is_file() for p in phase_dir.iterdir()):
                report.error(
                    "E_EMPTY_PHASE_DIR",
                    f"{phase_dir.name}/ contains no files.",
                    f"{study_rel}/dicom/{phase_dir.name}",
                )
        return len(layout.dicom_dirs)

    if form == LAYOUT_SINGLE_4D and layout.single_4d is not None:
        try:
            header = read_header(layout.single_4d)
        except VolumeReadError as exc:
            report.error(
                "E_VOLUME_UNREADABLE", f"{SINGLE_4D_NAME} {exc}", f"{study_rel}/{SINGLE_4D_NAME}"
            )
            return None
        if len(header.shape) < 4:
            report.error(
                "E_SINGLE_4D_NOT_4D",
                f"{SINGLE_4D_NAME} has shape {header.shape}, which is 3D. The single-file form "
                f"must carry the time axis as the 4th dimension.",
                f"{study_rel}/{SINGLE_4D_NAME}",
            )
            return None
        return header.n_timepoints

    return None


def _check_volume_geometry(
    layout: StudyLayout,
    form: str,
    study: dict[str, Any] | None,
    study_rel: str,
    report: Report,
) -> list[VolumeHeader]:
    """Headers only -- no pixel data is loaded, so this stays fast on large trees."""
    paths: list[Path] = []
    if layout.timepoint_files:
        paths = layout.timepoint_files
    elif layout.single_4d is not None:
        paths = [layout.single_4d]

    headers: list[VolumeHeader] = []
    for path in paths:
        try:
            headers.append(read_header(path))
        except VolumeReadError as exc:
            report.error("E_VOLUME_UNREADABLE", f"{path.name} {exc}", f"{study_rel}/{path.name}")

    for problem in compare_geometry(headers):
        report.error(problem["code"], problem["message"], study_rel)

    if headers and study is not None:
        _compare_declared_geometry(headers[0], study, study_rel, report)

    return headers


def _compare_declared_geometry(
    header: VolumeHeader, study: dict[str, Any], study_rel: str, report: Report
) -> None:
    geometry = study.get("geometry")
    if not isinstance(geometry, dict):
        return

    declared_in_plane = geometry.get("in_plane_mm")
    if isinstance(declared_in_plane, list) and len(declared_in_plane) == 2:
        actual = header.in_plane_mm
        if any(
            abs(float(d) - float(a)) > SPACING_TOLERANCE_MM
            for d, a in zip(declared_in_plane, actual)
        ):
            report.error(
                "E_GEOMETRY_MISMATCH",
                f"{STUDY_JSON} declares in_plane_mm {declared_in_plane} but the NIfTI header of "
                f"{header.path.name} says {[round(v, 6) for v in actual]}. The RFP resolution "
                f"thresholds are checked against the declared value, so the two must agree.",
                f"{study_rel}/{STUDY_JSON}",
            )

    declared_spacing = geometry.get("slice_spacing_mm")
    if isinstance(declared_spacing, (int, float)):
        actual_spacing = header.slice_spacing_mm
        if abs(float(declared_spacing) - actual_spacing) > SPACING_TOLERANCE_MM:
            report.error(
                "E_GEOMETRY_MISMATCH",
                f"{STUDY_JSON} declares slice_spacing_mm {declared_spacing} but the NIfTI "
                f"header of {header.path.name} says {round(actual_spacing, 6)}.",
                f"{study_rel}/{STUDY_JSON}",
            )

    declared_matrix = geometry.get("matrix")
    if isinstance(declared_matrix, list) and len(declared_matrix) == 3:
        actual_matrix = list(header.spatial_shape)
        if [int(v) for v in declared_matrix] != actual_matrix:
            report.error(
                "E_GEOMETRY_MISMATCH",
                f"{STUDY_JSON} declares matrix {declared_matrix} but the NIfTI header of "
                f"{header.path.name} says {actual_matrix}.",
                f"{study_rel}/{STUDY_JSON}",
            )


def _check_declared_derived(
    study_dir: Path, study: dict[str, Any] | None, study_rel: str, report: Report
) -> None:
    derived_dir = study_dir / "derived"
    if not derived_dir.is_dir():
        return
    declared = set()
    if study is not None and isinstance(study.get("derived"), list):
        declared = {entry.get("path") for entry in study["derived"] if isinstance(entry, dict)}
    for path in sorted(derived_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(study_dir).as_posix()
        if rel not in declared:
            report.error(
                "E_UNDECLARED_DERIVED",
                f"{rel} exists but is not listed in {STUDY_JSON}.derived. Derived products must "
                f"be declared so a downstream user knows what they are and how they were made.",
                f"{study_rel}/{rel}",
            )


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


# --- patient ------------------------------------------------------------------


def _verify_patient(
    root: Path,
    patient_id: str,
    study_dirs: dict[str, Path],
    ehr_dir: Path | None,
    report: Report,
) -> dict[str, Any]:
    """Check the suffix sequence and the patient-level sidecar."""
    indices = sorted(naming.suffix_to_index(suffix) for suffix in study_dirs)
    if indices and indices != list(range(1, len(indices) + 1)):
        present = sorted(study_dirs)
        expected = [naming.index_to_suffix(i) for i in range(1, len(indices) + 1)]
        report.error(
            "E_STUDY_SUFFIX_GAP",
            f"{patient_id} has study suffixes {present} but they must form the contiguous "
            f"sequence starting at A ({expected}). A gap almost always means a study directory "
            f"was lost in transfer rather than deliberately skipped.",
            patient_id,
        )

    patient: dict[str, Any] | None = None
    if ehr_dir is None:
        report.error(
            "E_MISSING_EHR_DIR",
            f"{patient_id}_ehr/ is missing. Every patient needs one, holding patient.json with "
            f"the demographics, disease state, vitals and reason for scan the RFP asks for.",
            f"{patient_id}_ehr",
        )
    else:
        patient_json_path = ehr_dir / PATIENT_JSON
        if not patient_json_path.is_file():
            report.error(
                "E_MISSING_PATIENT_JSON",
                f"{PATIENT_JSON} is missing from {ehr_dir.name}/. Copy "
                f"skills/openh-4d-shared/patient-json-template.json and fill it in.",
                f"{ehr_dir.name}/{PATIENT_JSON}",
            )
        else:
            patient, problem = _load_json(patient_json_path)
            if problem:
                report.error(
                    "E_PATIENT_JSON_UNREADABLE",
                    f"{PATIENT_JSON} {problem}",
                    f"{ehr_dir.name}/{PATIENT_JSON}",
                )

    if patient is not None:
        from .schema import validate_patient

        for finding in validate_patient(patient):
            report.error(
                finding.code,
                f"{PATIENT_JSON}: {finding.message}"
                + (f" (at {finding.path})" if finding.path else ""),
                f"{patient_id}_ehr/{PATIENT_JSON}",
            )

        if patient.get("patient_id") != patient_id:
            report.error(
                "E_PATIENT_ID_MISMATCH",
                f"{PATIENT_JSON} declares patient_id {patient.get('patient_id')!r} but the "
                f"directory is named {patient_id}_ehr.",
                f"{patient_id}_ehr/{PATIENT_JSON}",
            )

        declared = patient.get("studies")
        if isinstance(declared, list):
            on_disk = {f"{patient_id}{suffix}" for suffix in study_dirs}
            missing = sorted(set(declared) - on_disk)
            extra = sorted(on_disk - set(declared))
            if missing:
                report.error(
                    "E_PATIENT_STUDIES_MISSING",
                    f"{PATIENT_JSON} lists {missing}, but those study directories do not exist.",
                    f"{patient_id}_ehr/{PATIENT_JSON}",
                )
            if extra:
                report.error(
                    "E_PATIENT_STUDIES_UNLISTED",
                    f"study directories {extra} exist but are not listed in "
                    f"{PATIENT_JSON}.studies.",
                    f"{patient_id}_ehr/{PATIENT_JSON}",
                )

    return {"patient_id": patient_id, "n_studies": len(study_dirs)}


# --- top level ----------------------------------------------------------------


def verify(root: Path, only_study: str | None = None) -> Report:
    """Verify a submission root. Returns a :class:`Report`; never raises on bad input."""
    root = Path(root)
    report = Report(root=root)

    if not root.is_dir():
        report.error("E_ROOT_NOT_A_DIRECTORY", f"{root} is not a directory", str(root))
        return report

    entries = sorted(root.iterdir())
    dir_names = [e.name for e in entries if e.is_dir()]
    _check_case_collisions(dir_names, root, report)
    _check_forbidden_names(root, report)

    if only_study is None:
        _check_root_files(root, report)

    studies_by_patient: dict[str, dict[str, Path]] = {}
    ehr_by_patient: dict[str, Path] = {}

    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            parsed = naming.parse_dir_name(entry.name)
        except InvalidNameError as exc:
            report.error("E_INVALID_DIR_NAME", str(exc), entry.name)
            continue
        if parsed.kind == KIND_STUDY:
            studies_by_patient.setdefault(parsed.patient_id, {})[parsed.suffix] = entry
        elif parsed.kind == KIND_EHR:
            ehr_by_patient[parsed.patient_id] = entry

    for patient_id, ehr_dir in sorted(ehr_by_patient.items()):
        if patient_id not in studies_by_patient:
            report.error(
                "E_EHR_WITHOUT_STUDIES",
                f"{ehr_dir.name}/ exists but {patient_id} has no study directories.",
                ehr_dir.name,
            )

    if not studies_by_patient:
        report.error(
            "E_NO_STUDIES",
            f"no study directories found under {root}. A study directory is named "
            f"<PATIENT_ID><LETTER>, for example STAN-0001A.",
            "",
        )
        report.summary = {"patients": 0, "studies": 0, "timepoints": 0, "bytes": 0}
        return report

    study_summaries = []
    patient_summaries = []
    for patient_id, study_dirs in sorted(studies_by_patient.items()):
        if only_study is None:
            patient_summaries.append(
                _verify_patient(
                    root, patient_id, study_dirs, ehr_by_patient.get(patient_id), report
                )
            )
        for suffix, study_dir in sorted(study_dirs.items()):
            study_id = f"{patient_id}{suffix}"
            if only_study is not None and study_id != only_study:
                continue
            summary = _verify_study(study_dir, root, report, patient_id, study_id)
            if summary:
                study_summaries.append(summary)

    if only_study is not None and not study_summaries:
        report.error(
            "E_STUDY_NOT_FOUND",
            f"no study directory named {only_study!r} under {root}",
            only_study,
        )

    report.summary = {
        "patients": len(patient_summaries) if only_study is None else 1,
        "studies": len(study_summaries),
        "timepoints": sum(s["n_timepoints"] or 0 for s in study_summaries),
        "bytes": sum(s["bytes"] for s in study_summaries),
        "modalities": _tally(study_summaries, "modality"),
        "organs": _tally(study_summaries, "organ"),
    }
    return report


def _tally(summaries: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for summary in summaries:
        value = summary.get(key)
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    from . import __version__

    parser = argparse.ArgumentParser(
        description="Verify a directory against the OpenH-4D submission layout."
    )
    parser.add_argument("root", type=Path, help="the submission root directory")
    parser.add_argument(
        "--study",
        metavar="STUDY_ID",
        help="verify only this study (e.g. STAN-0001A); skips root and patient-level checks",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the compliant flag and counts, not individual findings",
    )
    args = parser.parse_args(argv)

    report = verify(args.root, only_study=args.study)
    output = report.as_dict(__version__)
    if args.quiet:
        output = {k: output[k] for k in ("root", "compliant", "summary")}
        output["n_errors"] = len(report.errors)
        output["n_warnings"] = len(report.warnings)
    print(json.dumps(output, indent=2))
    return 0 if report.compliant else 1


if __name__ == "__main__":
    sys.exit(main())
