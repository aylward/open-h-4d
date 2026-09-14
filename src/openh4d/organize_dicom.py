# SPDX-License-Identifier: Apache-2.0
"""Apply a DICOM index plan, writing the OpenH-4D submission layout.

Takes the plan proposed by :mod:`openh4d.dicom_index` (and confirmed by a human)
and materializes it: one directory per 4D study named ``<PATIENT_ID><LETTER>``,
one ``<PATIENT_ID>_ehr`` per patient, DICOM instances filed into per-phase
subdirectories, and ``study.json`` / ``patient.json`` stubs carrying everything
derivable from the headers.

Three things this module is deliberate about:

**Identifiers.** The submission never contains the source ``PatientID``. Each
patient is assigned ``<PREFIX>-NNNN`` using the prefix the steering group issued
to the contributor, so identifiers stay unique when submissions are merged.
Study letters follow acquisition order, so re-running on the same input always
yields the same letters -- a published letter is part of a citable identifier.

**The crosswalk.** The mapping back to source identifiers is written *outside*
the submission root, under the tool's cache directory, and the tool refuses to
write it inside. A ``zip -r`` of the submission's parent directory must not be
able to sweep one up.

**Tag retention.** When DICOM is copied into the submission, only tags on the
allowlist in ``dicom_allowlist.json`` survive. This is defense in depth on top of
contributor-side de-identification, not a substitute for it.

Usage::

    python -m openh4d.dicom_index <src> --out plan.json
    python -m openh4d.organize_dicom --plan plan.json --out <root> --id-prefix STAN
    python -m openh4d.organize_dicom --plan plan.json --out <root> --id-prefix STAN --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import naming
from .paths import crosswalk_dir, ensure_dir

ALLOWLIST_PATH = Path(__file__).parent / "dicom_allowlist.json"

CROSSWALK_COLUMNS = [
    "oh4d_patient_id",
    "oh4d_study_id",
    "source_patient_id",
    "source_study_instance_uid",
    "source_series_instance_uids",
    "assigned_at",
]


class OrganizeError(RuntimeError):
    """Organizing could not proceed. The message says what to do about it."""


@dataclass
class Assignment:
    """What one source study becomes in the submission."""

    source_patient_id: str
    source_study_uid: str
    patient_id: str
    study_id: str
    n_timepoints: int | None
    n_files: int
    modality: str | None
    series_uids: list[str] = field(default_factory=list)


def load_allowlist() -> set[str]:
    """Keywords that survive the copy, parsed from the annotated allowlist file."""
    document = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    keywords = set()
    for group in document["keep"].values():
        for entry in group:
            # Entries read "GGGG,EEEE Keyword"; the keyword is what pydicom uses.
            parts = entry.split(None, 1)
            if len(parts) == 2:
                keywords.add(parts[1].strip())
    return keywords


def apply_allowlist(dataset, keywords: set[str]):
    """Strip every element not on the allowlist, in place. Returns the dataset."""
    import pydicom

    for element in list(dataset):
        if element.tag == pydicom.tag.Tag(0x7FE0, 0x0010):  # PixelData
            continue
        if element.tag.is_private or element.keyword not in keywords:
            del dataset[element.tag]
    return dataset


# --- identifier assignment ----------------------------------------------------


def assign_identifiers(
    plan: dict[str, Any], prefix: str, start: int = 1, width: int = 4
) -> list[Assignment]:
    """Map every planned study to its OpenH-4D patient and study identifier.

    Patients are numbered in sorted source-identifier order and studies lettered
    in the plan's order (which :mod:`openh4d.dicom_index` already sorted by
    acquisition date), so the mapping is reproducible across runs.
    """
    assignments: list[Assignment] = []
    for offset, patient in enumerate(
        sorted(plan["patients"], key=lambda p: p["source_patient_id"])
    ):
        patient_id = naming.make_patient_id(prefix, start + offset, width=width)
        for index, study in enumerate(patient["studies"], start=1):
            series_uids = sorted(
                {
                    uid
                    for timepoint in study.get("timepoints", [])
                    for uid in timepoint.get("series_uids", [])
                }
            )
            assignments.append(
                Assignment(
                    source_patient_id=patient["source_patient_id"],
                    source_study_uid=study["study_instance_uid"],
                    patient_id=patient_id,
                    study_id=naming.study_dir_name(patient_id, index),
                    n_timepoints=study.get("n_timepoints"),
                    n_files=study.get("n_files", 0),
                    modality=study.get("modality"),
                    series_uids=series_uids,
                )
            )
    return assignments


def resolve_crosswalk_path(out_root: Path, explicit: Path | None, submission_id: str) -> Path:
    """Where the crosswalk goes, refusing anywhere inside the submission."""
    path = Path(explicit) if explicit else crosswalk_dir() / f"{submission_id}.csv"
    path = path.expanduser().resolve()
    root = Path(out_root).expanduser().resolve()
    if path == root or root in path.parents:
        raise OrganizeError(
            f"refusing to write the crosswalk to {path}: it is inside the submission root "
            f"{root}. The crosswalk maps OpenH-4D identifiers back to real patients and must "
            f"never travel with the data. Leave --crosswalk unset to use "
            f"{crosswalk_dir()}, or point it somewhere outside the submission."
        )
    return path


def write_crosswalk(path: Path, assignments: list[Assignment]) -> Path:
    """Append the identifier mapping, creating the file with a header if needed."""
    from .synthetic import utc_now

    ensure_dir(path.parent)
    exists = path.is_file()
    timestamp = utc_now()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(CROSSWALK_COLUMNS)
        for assignment in assignments:
            writer.writerow(
                [
                    assignment.patient_id,
                    assignment.study_id,
                    assignment.source_patient_id,
                    assignment.source_study_uid,
                    ";".join(assignment.series_uids),
                    timestamp,
                ]
            )
    return path


# --- sidecar stubs ------------------------------------------------------------

#: Fields nothing in the pixel data can supply. The organize skill interviews the
#: contributor for these; the tooling must never invent them.
REQUIRES_CONTRIBUTOR = "REQUIRES_CONTRIBUTOR"


def study_stub(
    assignment: Assignment, study_plan: dict[str, Any], *, keep_dicom: bool
) -> dict[str, Any]:
    """A ``study.json`` carrying everything the headers gave us and nothing more."""
    from . import __version__
    from .synthetic import utc_now

    geometry = study_plan.get("geometry") or {}
    multiframe = bool(study_plan.get("multiframe"))
    timepoints = study_plan.get("timepoints", [])

    modality = assignment.modality if assignment.modality in {"CT", "MR", "US"} else "CT"

    study: dict[str, Any] = {
        "schema": "openh-4d/study/1.0",
        "study_id": assignment.study_id,
        "patient_id": assignment.patient_id,
        "modality": modality,
        # Neither organ nor motion is derivable from a DICOM header; the skill
        # interviews the contributor and the verifier rejects the placeholder.
        "organ": None,
        "motion": None,
        "protocol": None,
        "clinical_indication": None,
        "file_layout": "dicom_phases" if keep_dicom else "timepoint_volumes",
        "n_timepoints": assignment.n_timepoints,
        "timepoints": [
            {
                "index": timepoint["index"],
                "file": (
                    f"dicom/t{timepoint['index']:04d}"
                    if keep_dicom
                    else f"t{timepoint['index']:04d}.nii.gz"
                ),
                "phase_label": timepoint.get("label"),
                "phase_type": None,
                "phase_fraction": timepoint.get("phase_fraction"),
                "trigger_time_ms": timepoint.get("trigger_time_ms"),
                "acquisition_time_s": None,
            }
            for timepoint in timepoints
        ],
        "coverage": None,
        "coverage_notes": "",
        "contrast": {"used": None, "agent": None, "phase": None},
        "geometry": {
            "in_plane_mm": geometry.get("in_plane_mm"),
            "slice_spacing_mm": geometry.get("slice_spacing_mm"),
            "matrix": geometry.get("matrix"),
            "temporal_resolution_ms": None,
            "orientation": None,
        },
        "gating": {
            "method": "unknown",
            "phase_source": study_plan.get("phase_source"),
        },
        "annotations": {"segmentations": [], "landmarks": [], "categorizations": []},
        "derived": [],
        "source": {
            "original_format": "DICOM",
            "converter": None,
            "converter_args": None,
            "converted_at": utc_now(),
            "n_source_files": assignment.n_files,
        },
        "waivers": [],
        "notes": (
            f"Stub written by openh4d {__version__} organize_dicom. Fields left null are not "
            f"derivable from DICOM headers and must be supplied by the contributor: organ, "
            f"motion, coverage, protocol, clinical_indication, contrast."
        ),
    }
    if multiframe:
        study["multiframe"] = True
    if modality == "CT":
        study["ct"] = {"low_dose": None, "kvp": None, "exposure_mas": None, "recon_kernel": None}
    elif modality == "MR":
        study["mr"] = {
            "sequence": None,
            "field_strength_t": None,
            "tr_ms": None,
            "te_ms": None,
            "other_sequences_included": [],
        }
    else:
        study["us"] = {
            "source": None,
            "probe": None,
            "frame_rate_hz": None,
            "vendor_decoder": None,
        }
    return study


def patient_stub(patient_id: str, study_ids: list[str]) -> dict[str, Any]:
    """A ``patient.json`` with every clinical field flagged for the contributor."""
    return {
        "schema": "openh-4d/patient/1.0",
        "patient_id": patient_id,
        "studies": study_ids,
        "demographics": {
            "age_years": None,
            "age_90_or_older": False,
            "sex": "unknown",
            "gender": None,
            "height_cm": None,
            "weight_kg": None,
            "bmi": None,
            "ethnicity": None,
        },
        "disease_state": {
            "primary_diagnosis": None,
            "codes": [],
            "severity": None,
            "comorbidities": [],
            "notes": REQUIRES_CONTRIBUTOR,
        },
        "vitals_at_acquisition": {
            "systolic_bp_mmhg": None,
            "diastolic_bp_mmhg": None,
            "heart_rate_bpm": None,
            "respiratory_rate_bpm": None,
            "spo2_percent": None,
            "temperature_c": None,
            "measured_relative_to_scan": "unknown",
        },
        "treatment_history": [],
        "reason_for_scan": {"category": "other", "description": REQUIRES_CONTRIBUTOR},
        "deidentification": {
            "standard": "hipaa_safe_harbor",
            "date_shift_applied": False,
            "date_shift_offset_disclosed": False,
            "performed_by": REQUIRES_CONTRIBUTOR,
            "tooling": REQUIRES_CONTRIBUTOR,
            "attestation": REQUIRES_CONTRIBUTOR,
        },
        "extra": {},
    }


# --- applying -----------------------------------------------------------------


def organize(
    plan: dict[str, Any],
    out_root: Path,
    *,
    prefix: str,
    start: int = 1,
    id_width: int = 4,
    keep_dicom: bool = True,
    crosswalk: Path | None = None,
    submission_id: str = "submission",
    apply: bool = False,
) -> dict[str, Any]:
    """Materialize ``plan`` under ``out_root``.

    With ``apply=False`` (the default) nothing is written except the report, so a
    contributor can see exactly what would happen first.
    """
    out_root = Path(out_root)
    source_root = Path(plan["source"])
    assignments = assign_identifiers(plan, prefix, start=start, width=id_width)
    crosswalk_path = resolve_crosswalk_path(out_root, crosswalk, submission_id)

    plan_by_key = {
        (patient["source_patient_id"], study["study_instance_uid"]): study
        for patient in plan["patients"]
        for study in patient["studies"]
    }

    actions: list[dict[str, Any]] = []
    blocked: list[str] = []
    keywords = load_allowlist() if keep_dicom else set()

    for assignment in assignments:
        study_plan = plan_by_key[(assignment.source_patient_id, assignment.source_study_uid)]
        if study_plan.get("n_timepoints") is None and not study_plan.get("multiframe"):
            blocked.append(
                f"{assignment.study_id}: the indexer could not recover time points "
                f"({'; '.join(study_plan.get('problems', [])) or 'no reason recorded'})"
            )
            continue

        study_dir = out_root / assignment.study_id
        n_copied = 0
        if apply:
            study_dir.mkdir(parents=True, exist_ok=True)

        if keep_dicom:
            if study_plan.get("multiframe"):
                targets = [(study_dir / "dicom", study_plan.get("files", []))]
            else:
                targets = [
                    (study_dir / "dicom" / f"t{timepoint['index']:04d}", timepoint["files"])
                    for timepoint in study_plan["timepoints"]
                ]
            for target_dir, files in targets:
                if apply:
                    target_dir.mkdir(parents=True, exist_ok=True)
                for position, relative in enumerate(files):
                    destination = target_dir / f"{position:05d}.dcm"
                    if apply:
                        _copy_allowlisted(source_root / relative, destination, keywords, assignment)
                    n_copied += 1

        if apply:
            _write_json(
                study_dir / "study.json",
                study_stub(assignment, study_plan, keep_dicom=keep_dicom),
            )

        actions.append(
            {
                "study_id": assignment.study_id,
                "patient_id": assignment.patient_id,
                "n_timepoints": assignment.n_timepoints,
                "n_files": n_copied,
                "file_layout": "dicom_phases" if keep_dicom else "timepoint_volumes",
            }
        )

    studies_by_patient: dict[str, list[str]] = {}
    for assignment in assignments:
        if any(a["study_id"] == assignment.study_id for a in actions):
            studies_by_patient.setdefault(assignment.patient_id, []).append(assignment.study_id)

    for patient_id, study_ids in sorted(studies_by_patient.items()):
        ehr_dir = out_root / naming.ehr_dir_name(patient_id)
        if apply:
            ehr_dir.mkdir(parents=True, exist_ok=True)
            _write_json(ehr_dir / "patient.json", patient_stub(patient_id, sorted(study_ids)))

    if apply and actions:
        write_crosswalk(
            crosswalk_path,
            [a for a in assignments if a.study_id in {x["study_id"] for x in actions}],
        )

    return {
        "applied": apply,
        "out_root": str(out_root),
        "id_prefix": prefix,
        "crosswalk": str(crosswalk_path),
        "n_patients": len(studies_by_patient),
        "n_studies": len(actions),
        "studies": actions,
        "blocked": blocked,
        "next_steps": [
            "Fill in organ, motion, coverage, protocol and clinical_indication in each "
            "study.json -- none of these are in the DICOM headers.",
            "Fill in patient.json: demographics, disease state, vitals, treatment history, "
            "reason for scan, and the de-identification attestation.",
            "Convert to NIfTI: python -m openh4d.convert_dicom <study-dir>",
            f"Verify: python -m openh4d.verify_layout {out_root}",
        ],
    }


def _copy_allowlisted(
    source: Path, destination: Path, keywords: set[str], assignment: Assignment
) -> None:
    """Copy one instance, keeping only allowlisted tags and re-keying the patient."""
    import pydicom

    dataset = pydicom.dcmread(str(source))
    apply_allowlist(dataset, keywords)
    dataset.PatientID = assignment.patient_id
    dataset.save_as(str(destination), enforce_file_format=True)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="plan.json from dicom_index")
    parser.add_argument("--out", type=Path, required=True, help="submission root to write")
    parser.add_argument(
        "--id-prefix",
        default="OH4D",
        help="identifier prefix issued to you by the steering group (default OH4D, which is "
        "for local experiments only)",
    )
    parser.add_argument("--id-start", type=int, default=1, help="first patient number")
    parser.add_argument("--id-width", type=int, default=4, help="zero-padded digits")
    parser.add_argument(
        "--no-keep-dicom",
        action="store_true",
        help="do not copy DICOM into the submission; convert to NIfTI only",
    )
    parser.add_argument(
        "--crosswalk",
        type=Path,
        help="where to write the identifier crosswalk (must be outside --out)",
    )
    parser.add_argument("--submission-id", default="submission")
    parser.add_argument(
        "--apply", action="store_true", help="actually write files (default is a dry run)"
    )
    args = parser.parse_args(argv)

    if not args.plan.is_file():
        print(f"no such plan: {args.plan}", file=sys.stderr)
        return 1

    plan = json.loads(args.plan.read_text(encoding="utf-8"))

    try:
        report = organize(
            plan,
            args.out,
            prefix=args.id_prefix,
            start=args.id_start,
            id_width=args.id_width,
            keep_dicom=not args.no_keep_dicom,
            crosswalk=args.crosswalk,
            submission_id=args.submission_id,
            apply=args.apply,
        )
    except (OrganizeError, naming.InvalidNameError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2))
    if not args.apply:
        print("\nDry run. Re-run with --apply to write these files.", file=sys.stderr)
    if report["blocked"]:
        print(
            f"\n{len(report['blocked'])} study(ies) were not organized; see 'blocked'.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
