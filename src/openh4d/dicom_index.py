# SPDX-License-Identifier: Apache-2.0
"""Index a pile of DICOM files and work out what the 4D studies are.

This is the highest-risk logic in OpenH-4D. Everything downstream inherits its
decision about what constitutes one 4D study and which image belongs to which
time point, and a wrong answer is invisible in the output: the volumes still
open, the geometry still looks fine, and the motion is simply wrong.

Two things follow from that. First, this module only ever *proposes* a plan --
applying it is a separate step, and the agent skill requires a human to confirm
the grouping. Second, when two heuristics disagree about how many time points
there are, the disagreement is reported rather than resolved. Silently picking
one is the worst failure mode available here.

Only headers are read (``stop_before_pixels=True``), so indexing 100,000 files
is fast.

Usage::

    python -m openh4d.dicom_index <source-dir> --out plan.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = "openh-4d/dicom-plan/1.0"

#: Respiratory or cardiac phase written into the series description, as TCIA
#: 4D-Lung and many vendor 4D CT exports do ("... 50.0%", "Ex", "In").
PHASE_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
PHASE_WORD_RE = re.compile(r"\b(ex|in)(?:hale|)\b", re.IGNORECASE)

#: Confidence levels attached to a recovered time-point grouping.
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

#: Rungs that need a human to say yes before they may be applied.
REQUIRES_CONFIRMATION = {"InstanceNumber"}


@dataclass
class DicomFile:
    """The header fields needed to group and order one DICOM instance."""

    path: Path
    patient_id: str | None = None
    patient_name: str | None = None
    birth_date: str | None = None
    study_uid: str | None = None
    series_uid: str | None = None
    study_date: str | None = None
    study_time: str | None = None
    modality: str | None = None
    series_description: str | None = None
    series_number: int | None = None
    instance_number: int | None = None
    n_frames: int = 1
    rows: int | None = None
    columns: int | None = None
    pixel_spacing: tuple[float, float] | None = None
    slice_thickness: float | None = None
    spacing_between_slices: float | None = None
    image_orientation: tuple[float, ...] | None = None
    image_position: tuple[float, ...] | None = None
    temporal_position: int | None = None
    n_temporal_positions: int | None = None
    trigger_time: float | None = None
    cardiac_phase_percent: float | None = None
    acquisition_number: int | None = None
    content_time: str | None = None

    @property
    def is_multiframe(self) -> bool:
        return self.n_frames > 1

    def slice_offset(self) -> float:
        """Signed distance along the slice normal, for ordering within a phase.

        ``SliceLocation`` is not used: its sign is unreliable across vendors, and
        ``InstanceNumber`` reflects transfer order rather than geometry.
        """
        if self.image_orientation is None or self.image_position is None:
            return 0.0
        orientation = np.asarray(self.image_orientation, dtype=float)
        normal = np.cross(orientation[:3], orientation[3:6])
        return float(np.dot(np.asarray(self.image_position, dtype=float), normal))

    def geometry_key(self) -> tuple:
        """Identifies series that are phases of one 4D acquisition."""
        return (
            self.rows,
            self.columns,
            self.pixel_spacing,
            tuple(round(v, 4) for v in self.image_orientation) if self.image_orientation else None,
        )


def _as_float_tuple(value) -> tuple[float, ...] | None:
    if value is None:
        return None
    try:
        return tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def read_dicom_header(path: Path) -> DicomFile | None:
    """Read one file's header. Returns ``None`` if it is not DICOM."""
    import pydicom
    from pydicom.errors import InvalidDicomError

    try:
        dataset = pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
    except (InvalidDicomError, OSError, AttributeError):
        return None
    except Exception:  # noqa: BLE001 - a malformed file is skipped, not fatal
        return None

    spacing = _as_float_tuple(dataset.get("PixelSpacing"))
    return DicomFile(
        path=path,
        patient_id=_str(dataset.get("PatientID")),
        patient_name=_str(dataset.get("PatientName")),
        birth_date=_str(dataset.get("PatientBirthDate")),
        study_uid=_str(dataset.get("StudyInstanceUID")),
        series_uid=_str(dataset.get("SeriesInstanceUID")),
        study_date=_str(dataset.get("StudyDate")),
        study_time=_str(dataset.get("StudyTime")),
        modality=_str(dataset.get("Modality")),
        series_description=_str(dataset.get("SeriesDescription")),
        series_number=_as_int(dataset.get("SeriesNumber")),
        instance_number=_as_int(dataset.get("InstanceNumber")),
        n_frames=_as_int(dataset.get("NumberOfFrames")) or 1,
        rows=_as_int(dataset.get("Rows")),
        columns=_as_int(dataset.get("Columns")),
        pixel_spacing=(spacing[0], spacing[1]) if spacing and len(spacing) >= 2 else None,
        slice_thickness=_as_float(dataset.get("SliceThickness")),
        spacing_between_slices=_as_float(dataset.get("SpacingBetweenSlices")),
        image_orientation=_as_float_tuple(dataset.get("ImageOrientationPatient")),
        image_position=_as_float_tuple(dataset.get("ImagePositionPatient")),
        temporal_position=_as_int(dataset.get("TemporalPositionIdentifier")),
        n_temporal_positions=_as_int(dataset.get("NumberOfTemporalPositions")),
        trigger_time=_as_float(dataset.get("TriggerTime")),
        cardiac_phase_percent=_as_float(dataset.get("NominalPercentageOfCardiacPhase")),
        acquisition_number=_as_int(dataset.get("AcquisitionNumber")),
        content_time=_str(dataset.get("ContentTime")),
    )


def _str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def scan(source: Path) -> tuple[list[DicomFile], list[str]]:
    """Read every readable DICOM header under ``source``.

    Returns ``(files, skipped)``. Anything that is not DICOM is skipped rather
    than raising: contributor exports routinely contain DICOMDIR files, thumbnails
    and stray notes.
    """
    files: list[DicomFile] = []
    skipped: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        header = read_dicom_header(path)
        if header is None:
            skipped.append(str(path.relative_to(source)))
        else:
            files.append(header)
    return files, skipped


# --- the time-point recovery ladder ------------------------------------------


@dataclass
class Grouping:
    """One heuristic's answer to "which files form which time point"."""

    source: str
    """Name of the tag or rule that produced this grouping."""

    groups: dict[Any, list[DicomFile]]
    labels: dict[Any, str]
    confidence: str = CONFIDENCE_MEDIUM

    @property
    def n_timepoints(self) -> int:
        return len(self.groups)

    def ordered_keys(self) -> list[Any]:
        return sorted(self.groups)


def _by_temporal_position(files: list[DicomFile]) -> Grouping | None:
    """``TemporalPositionIdentifier`` -- the cleanest signal when it is present."""
    if not all(f.temporal_position is not None for f in files):
        return None
    groups: dict[Any, list[DicomFile]] = defaultdict(list)
    for f in files:
        groups[f.temporal_position].append(f)
    if len(groups) < 2:
        return None
    declared = {f.n_temporal_positions for f in files if f.n_temporal_positions}
    confidence = CONFIDENCE_HIGH if declared == {len(groups)} or not declared else CONFIDENCE_MEDIUM
    return Grouping(
        source="TemporalPositionIdentifier",
        groups=dict(groups),
        labels={k: f"temporal position {k}" for k in groups},
        confidence=confidence,
    )


def phase_from_description(description: str | None) -> tuple[float | None, str | None]:
    """Extract a phase fraction and its verbatim label from a series description.

    TCIA 4D-Lung and many vendor exports encode the respiratory phase here, as
    ``... 0.0%`` through ``... 90.0%``. The verbatim label is kept alongside the
    fraction so ``study.json`` can record what the source actually said.
    """
    if not description:
        return None, None
    match = PHASE_PERCENT_RE.search(description)
    if match:
        percent = float(match.group(1))
        if 0.0 <= percent <= 100.0:
            return percent / 100.0, match.group(0).strip()
    word = PHASE_WORD_RE.search(description)
    if word:
        # Exhale and inhale are the extremes of the respiratory cycle.
        return (0.0 if word.group(1).lower() == "ex" else 0.5), word.group(0)
    return None, None


def _by_series_description_phase(files: list[DicomFile]) -> Grouping | None:
    """A phase token in ``SeriesDescription``, one series per time point."""
    per_series: dict[str, list[DicomFile]] = defaultdict(list)
    for f in files:
        if f.series_uid:
            per_series[f.series_uid].append(f)

    groups: dict[Any, list[DicomFile]] = {}
    labels: dict[Any, str] = {}
    for series_files in per_series.values():
        fraction, label = phase_from_description(series_files[0].series_description)
        if fraction is None:
            return None
        if fraction in groups:
            # Two series claiming the same phase is not a clean one-per-phase set.
            return None
        groups[fraction] = series_files
        labels[fraction] = label or f"{fraction * 100:g}%"

    if len(groups) < 2:
        return None
    return Grouping(
        source="SeriesDescription",
        groups=groups,
        labels=labels,
        confidence=CONFIDENCE_HIGH,
    )


def _by_cardiac_phase(files: list[DicomFile]) -> Grouping | None:
    """``NominalPercentageOfCardiacPhase`` from enhanced cardiac DICOM."""
    if not all(f.cardiac_phase_percent is not None for f in files):
        return None
    groups: dict[Any, list[DicomFile]] = defaultdict(list)
    for f in files:
        groups[round(float(f.cardiac_phase_percent), 3)].append(f)
    if len(groups) < 2:
        return None
    return Grouping(
        source="NominalPercentageOfCardiacPhase",
        groups=dict(groups),
        labels={k: f"{k:g}%" for k in groups},
        confidence=CONFIDENCE_HIGH,
    )


def _by_trigger_time(files: list[DicomFile]) -> Grouping | None:
    """Distinct ``TriggerTime`` values, as cardiac-gated CT and MR produce."""
    if not all(f.trigger_time is not None for f in files):
        return None
    groups: dict[Any, list[DicomFile]] = defaultdict(list)
    for f in files:
        groups[round(float(f.trigger_time), 3)].append(f)
    if len(groups) < 2:
        return None
    return Grouping(
        source="TriggerTime",
        groups=dict(groups),
        labels={k: f"{k:g} ms" for k in groups},
        confidence=CONFIDENCE_MEDIUM,
    )


def _by_acquisition_number(files: list[DicomFile]) -> Grouping | None:
    if not all(f.acquisition_number is not None for f in files):
        return None
    groups: dict[Any, list[DicomFile]] = defaultdict(list)
    for f in files:
        groups[f.acquisition_number].append(f)
    if len(groups) < 2:
        return None
    return Grouping(
        source="AcquisitionNumber",
        groups=dict(groups),
        labels={k: f"acquisition {k}" for k in groups},
        confidence=CONFIDENCE_MEDIUM,
    )


def _by_content_time(files: list[DicomFile]) -> Grouping | None:
    """Distinct ``ContentTime`` values. Weak: helical 4D CT acquires continuously."""
    if not all(f.content_time for f in files):
        return None
    groups: dict[Any, list[DicomFile]] = defaultdict(list)
    for f in files:
        groups[f.content_time].append(f)
    if len(groups) < 2 or len(groups) == len(files):
        return None
    return Grouping(
        source="ContentTime",
        groups=dict(groups),
        labels={k: str(k) for k in groups},
        confidence=CONFIDENCE_LOW,
    )


def _by_instance_number_modulo(files: list[DicomFile]) -> Grouping | None:
    """Split by position within a repeating slice count.

    The weakest rung by a wide margin: it assumes instances were written in
    volume-major order, and produces convincing-looking garbage when they were
    not. Flagged as requiring human confirmation and never applied silently.
    """
    if not all(f.instance_number is not None for f in files):
        return None
    offsets = {round(f.slice_offset(), 3) for f in files}
    n_slices = len(offsets)
    if n_slices < 2 or len(files) % n_slices != 0:
        return None
    n_timepoints = len(files) // n_slices
    if n_timepoints < 2:
        return None

    ordered = sorted(files, key=lambda f: f.instance_number or 0)
    groups: dict[Any, list[DicomFile]] = defaultdict(list)
    for position, f in enumerate(ordered):
        groups[position // n_slices].append(f)
    return Grouping(
        source="InstanceNumber",
        groups=dict(groups),
        labels={k: f"block {k}" for k in groups},
        confidence=CONFIDENCE_LOW,
    )


#: Tried in order. The first that fires is proposed; every other that also fires
#: is recorded so a disagreement about the time-point count is visible.
LADDER = (
    _by_temporal_position,
    _by_series_description_phase,
    _by_cardiac_phase,
    _by_trigger_time,
    _by_acquisition_number,
    _by_content_time,
    _by_instance_number_modulo,
)


def recover_timepoints(files: list[DicomFile]) -> tuple[Grouping | None, list[Grouping]]:
    """Run the whole ladder. Returns ``(chosen, all_that_fired)``."""
    fired = [grouping for rung in LADDER if (grouping := rung(files)) is not None]
    return (fired[0] if fired else None), fired


# --- study assembly -----------------------------------------------------------


def order_slices(files: list[DicomFile]) -> list[DicomFile]:
    """Sort one time point's files into geometric slice order."""
    return sorted(files, key=lambda f: (f.slice_offset(), f.instance_number or 0))


def _slice_spacing(files: list[DicomFile]) -> tuple[float | None, list[str]]:
    """Median slice spacing, plus any problems found in the slice positions."""
    problems: list[str] = []
    offsets = [round(f.slice_offset(), 4) for f in order_slices(files)]
    if len(offsets) < 2:
        first = files[0]
        return (first.spacing_between_slices or first.slice_thickness), problems

    duplicates = len(offsets) - len(set(offsets))
    if duplicates:
        problems.append(
            f"{duplicates} slice position(s) appear more than once within one time point, "
            f"which usually means two phases were merged"
        )

    deltas = np.diff(sorted(set(offsets)))
    if deltas.size == 0:
        return None, problems
    spacing = float(np.median(deltas))
    if deltas.size > 1 and float(np.max(np.abs(deltas - spacing))) > 0.01:
        problems.append(
            f"slice spacing is not uniform (min {float(deltas.min()):.4f} mm, "
            f"max {float(deltas.max()):.4f} mm); dcm2niix will also warn about this"
        )
    return spacing, problems


def _study_geometry(grouping: Grouping) -> dict[str, Any]:
    first_key = grouping.ordered_keys()[0]
    first_group = grouping.groups[first_key]
    reference = first_group[0]
    spacing, _ = _slice_spacing(first_group)
    return {
        "in_plane_mm": list(reference.pixel_spacing) if reference.pixel_spacing else None,
        "slice_spacing_mm": round(spacing, 6) if spacing else None,
        "matrix": (
            [reference.columns, reference.rows, len(first_group)]
            if reference.rows and reference.columns
            else None
        ),
    }


def plan_study(study_uid: str, files: list[DicomFile], source: Path) -> dict[str, Any]:
    """Build the plan entry for one DICOM study."""
    reference = files[0]
    multiframe = any(f.is_multiframe for f in files)
    problems: list[str] = []

    entry: dict[str, Any] = {
        "study_instance_uid": study_uid,
        "study_date": reference.study_date,
        "modality": reference.modality,
        "series_descriptions": sorted(
            {f.series_description for f in files if f.series_description}
        ),
        "n_files": len(files),
        "n_series": len({f.series_uid for f in files if f.series_uid}),
        "multiframe": multiframe,
    }

    if multiframe:
        # One container already holds every time point, so there is nothing to
        # split. convert_dicom hands it to dcm2niix whole.
        entry.update(
            {
                "phase_source": "PerFrameFunctionalGroupsSequence",
                "confidence": CONFIDENCE_MEDIUM,
                "n_timepoints": None,
                "timepoints": [],
                "files": [str(f.path.relative_to(source).as_posix()) for f in files],
                "problems": [
                    "enhanced/multi-frame DICOM: the time points live inside the container, so "
                    "they are not split into per-phase directories. The file layout will be "
                    "'dicom_phases' with multiframe true, and the time-point count comes from "
                    "the converted NIfTI."
                ],
                "alternatives": [],
                "requires_confirmation": True,
            }
        )
        return entry

    chosen, fired = recover_timepoints(files)

    if chosen is None:
        entry.update(
            {
                "phase_source": None,
                "confidence": CONFIDENCE_LOW,
                "n_timepoints": None,
                "timepoints": [],
                "problems": [
                    "no temporal tag identified time points. None of "
                    f"{', '.join(rung.__name__.removeprefix('_by_') for rung in LADDER)} "
                    "produced a grouping. This may be a 3D study rather than a 4D one, or the "
                    "phase information may live somewhere this indexer does not look."
                ],
                "alternatives": [],
                "requires_confirmation": True,
            }
        )
        return entry

    counts = {g.source: g.n_timepoints for g in fired}
    disagreeing = {s: n for s, n in counts.items() if n != chosen.n_timepoints}
    if disagreeing:
        problems.append(
            f"heuristics disagree on the time-point count: {chosen.source} says "
            f"{chosen.n_timepoints}, but {disagreeing}. This is not resolved automatically -- "
            f"confirm which is right before applying the plan."
        )

    timepoints = []
    slice_counts = set()
    for index, key in enumerate(chosen.ordered_keys()):
        group = order_slices(chosen.groups[key])
        spacing, slice_problems = _slice_spacing(group)
        problems.extend(f"time point {index}: {p}" for p in slice_problems)
        slice_counts.add(len(group))
        fraction, _ = phase_from_description(group[0].series_description)
        timepoints.append(
            {
                "index": index,
                "label": chosen.labels.get(key, str(key)),
                "phase_fraction": (
                    fraction
                    if fraction is not None
                    else (
                        float(key) if chosen.source == "NominalPercentageOfCardiacPhase" else None
                    )
                ),
                "trigger_time_ms": (
                    float(key) if chosen.source == "TriggerTime" else group[0].trigger_time
                ),
                "series_uids": sorted({f.series_uid for f in group if f.series_uid}),
                "n_files": len(group),
                "files": [str(f.path.relative_to(source).as_posix()) for f in group],
            }
        )

    if len(slice_counts) > 1:
        problems.append(
            f"time points have differing slice counts {sorted(slice_counts)}; every time point "
            f"of a 4D study must cover the same volume"
        )

    confidence = chosen.confidence
    if disagreeing or len(slice_counts) > 1:
        confidence = CONFIDENCE_LOW

    entry.update(
        {
            "phase_source": chosen.source,
            "confidence": confidence,
            "n_timepoints": chosen.n_timepoints,
            "timepoints": timepoints,
            "problems": problems,
            "alternatives": [
                {"source": g.source, "n_timepoints": g.n_timepoints}
                for g in fired
                if g.source != chosen.source
            ],
            "requires_confirmation": (
                chosen.source in REQUIRES_CONFIRMATION or confidence == CONFIDENCE_LOW
            ),
            "geometry": _study_geometry(chosen),
        }
    )
    return entry


def _patient_key(f: DicomFile) -> str:
    """Stable grouping key, falling back when ``PatientID`` is absent."""
    if f.patient_id:
        return f.patient_id
    if f.patient_name or f.birth_date:
        import hashlib

        material = f"{f.patient_name}|{f.birth_date}".encode()
        return f"UNKNOWN-{hashlib.sha256(material).hexdigest()[:12]}"
    return "UNKNOWN-NO-IDENTIFIER"


def build_plan(source: Path) -> dict[str, Any]:
    """Index ``source`` and propose a grouping into patients, studies and time points."""
    from . import __version__
    from .synthetic import utc_now

    source = Path(source)
    files, skipped = scan(source)

    by_patient: dict[str, dict[str, list[DicomFile]]] = defaultdict(lambda: defaultdict(list))
    for f in files:
        study_uid = f.study_uid or f"NO-STUDY-UID-{f.study_date}-{f.study_time}"
        by_patient[_patient_key(f)][study_uid].append(f)

    patients = []
    for patient_key in sorted(by_patient):
        studies = by_patient[patient_key]
        # Sorting by (date, time, uid) makes the letter assignment reproducible:
        # a published study letter is part of a citable identifier.
        ordered_uids = sorted(
            studies,
            key=lambda uid: (
                studies[uid][0].study_date or "",
                studies[uid][0].study_time or "",
                uid,
            ),
        )
        patients.append(
            {
                "source_patient_id": patient_key,
                "n_studies": len(studies),
                "studies": [plan_study(uid, studies[uid], source) for uid in ordered_uids],
            }
        )

    return {
        "schema": SCHEMA_VERSION,
        "source": str(source),
        "generated_by": f"openh4d {__version__}",
        "generated_at": utc_now(),
        "n_dicom_files": len(files),
        "n_skipped_files": len(skipped),
        "skipped_files": skipped[:50],
        "patients": patients,
    }


def summarize(plan: dict[str, Any]) -> str:
    """A short human-readable digest for the review step of the skill."""
    lines = [
        f"source: {plan['source']}",
        f"{plan['n_dicom_files']} DICOM file(s), {plan['n_skipped_files']} skipped",
        f"{len(plan['patients'])} patient(s)",
    ]
    for patient in plan["patients"]:
        lines.append(f"\n  {patient['source_patient_id']}: {patient['n_studies']} study(ies)")
        for index, study in enumerate(patient["studies"]):
            letter = chr(ord("A") + index) if index < 26 else "?"
            lines.append(
                f"    [{letter}] {study['modality']} {study['n_files']} files, "
                f"{study['n_series']} series, {study['n_timepoints']} time point(s) "
                f"via {study['phase_source']} ({study['confidence']} confidence)"
            )
            for alternative in study.get("alternatives", []):
                lines.append(
                    f"          also fired: {alternative['source']} -> "
                    f"{alternative['n_timepoints']} time point(s)"
                )
            for problem in study.get("problems", []):
                lines.append(f"          ! {problem}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="directory of DICOM files")
    parser.add_argument("--out", type=Path, help="write the plan here (default: stdout)")
    parser.add_argument(
        "--summary", action="store_true", help="print the human-readable digest instead of JSON"
    )
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        print(f"not a directory: {args.source}", file=sys.stderr)
        return 1

    plan = build_plan(args.source)

    if args.out:
        args.out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        print(summarize(plan), file=sys.stderr)
        print(f"\nwrote plan to {args.out}", file=sys.stderr)
    elif args.summary:
        print(summarize(plan))
    else:
        print(json.dumps(plan, indent=2))

    needs_review = any(
        study.get("requires_confirmation")
        for patient in plan["patients"]
        for study in patient["studies"]
    )
    return 2 if needs_review else 0


if __name__ == "__main__":
    sys.exit(main())
