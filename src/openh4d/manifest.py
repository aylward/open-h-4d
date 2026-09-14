# SPDX-License-Identifier: Apache-2.0
"""Build the submission-level ``manifest.json``.

The manifest is an index, not a checksum database. It hashes the JSON sidecars
only: metadata is what silently drifts between a contributor's copy and the
delivered one, whereas image bytes are covered by ``checksums.sha256`` when
transfer integrity is actually in question. Hashing multiple gigabytes on every
verification run would make the verifier too slow to use.

Usage::

    python -m openh4d.manifest <submission-root>
    python -m openh4d.manifest <submission-root> --submission-id stanford-cardiac-2027
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import naming
from .naming import KIND_EHR, KIND_STUDY, InvalidNameError

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = "openh-4d/manifest/1.0"


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, read in chunks so large files do not load into RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _count_timepoints(study_dir: Path, study: dict[str, Any]) -> int:
    """Prefer the declared count; fall back to what is on disk.

    The declared count is authoritative for enhanced/multi-frame DICOM, where
    one container file holds every time point and there is nothing to count.
    """
    declared = study.get("n_timepoints")
    if isinstance(declared, int):
        return declared
    n_files = len(list(study_dir.glob("t[0-9][0-9][0-9][0-9].nii.gz")))
    if n_files:
        return n_files
    dicom_dir = study_dir / "dicom"
    if dicom_dir.is_dir():
        return len([d for d in dicom_dir.iterdir() if d.is_dir()])
    return 0


def build_manifest(
    root: Path,
    *,
    submission_id: str | None = None,
    organization: str | None = None,
    contact: str | None = None,
    proposal_id: str | None = None,
) -> dict[str, Any]:
    """Walk ``root`` and produce a manifest document.

    Malformed directory names are skipped rather than raising: the manifest is a
    convenience, and ``verify_layout`` is where naming problems get reported.
    """
    from . import __version__
    from .synthetic import utc_now

    root = Path(root)
    studies_by_patient: dict[str, dict[str, Path]] = {}
    ehr_by_patient: dict[str, Path] = {}

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            parsed = naming.parse_dir_name(entry.name)
        except InvalidNameError:
            continue
        if parsed.kind == KIND_STUDY:
            studies_by_patient.setdefault(parsed.patient_id, {})[parsed.suffix] = entry
        elif parsed.kind == KIND_EHR:
            ehr_by_patient[parsed.patient_id] = entry

    patients: list[dict[str, Any]] = []
    total_timepoints = 0
    total_bytes = 0
    modality_counts: dict[str, int] = {}
    organ_counts: dict[str, int] = {}

    for patient_id, study_dirs in sorted(studies_by_patient.items()):
        ehr_dir = ehr_by_patient.get(patient_id)
        patient_json = ehr_dir / "patient.json" if ehr_dir else None

        study_entries = []
        for suffix, study_dir in sorted(
            study_dirs.items(), key=lambda kv: naming.suffix_to_index(kv[0])
        ):
            study_json_path = study_dir / "study.json"
            study: dict[str, Any] = {}
            if study_json_path.is_file():
                try:
                    loaded = json.loads(study_json_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        study = loaded
                except (json.JSONDecodeError, OSError):
                    study = {}

            n_timepoints = _count_timepoints(study_dir, study)
            size = _dir_size(study_dir)
            total_timepoints += n_timepoints
            total_bytes += size

            modality = study.get("modality")
            organ = study.get("organ")
            if modality:
                modality_counts[modality] = modality_counts.get(modality, 0) + 1
            if organ:
                organ_counts[organ] = organ_counts.get(organ, 0) + 1

            entry: dict[str, Any] = {
                "study_id": f"{patient_id}{suffix}",
                "modality": modality,
                "organ": organ,
                "file_layout": study.get("file_layout"),
                "n_timepoints": n_timepoints,
                "bytes": size,
                "study_json_sha256": (
                    sha256_file(study_json_path) if study_json_path.is_file() else None
                ),
            }
            study_entries.append(entry)

        patients.append(
            {
                "patient_id": patient_id,
                "ehr_dir": ehr_dir.name if ehr_dir else None,
                "patient_json_sha256": (
                    sha256_file(patient_json) if patient_json and patient_json.is_file() else None
                ),
                "studies": study_entries,
            }
        )

    manifest: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "submission_id": submission_id,
        "generated_by": f"openh4d {__version__}",
        "generated_at": utc_now(),
        "counts": {
            "patients": len(patients),
            "studies": sum(len(p["studies"]) for p in patients),
            "timepoints": total_timepoints,
            "bytes": total_bytes,
        },
        "modality_breakdown": dict(sorted(modality_counts.items())),
        "organ_breakdown": dict(sorted(organ_counts.items())),
        "patients": patients,
    }
    if organization or contact or proposal_id:
        manifest["contributor"] = {
            "organization": organization,
            "contact": contact,
            "proposal_id": proposal_id,
        }
    return manifest


def write_manifest(root: Path, manifest: dict[str, Any]) -> Path:
    """Write ``manifest`` to ``root/manifest.json`` and return the path."""
    path = Path(root) / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def write_checksums(root: Path) -> Path:
    """Write ``checksums.sha256`` over every file in the tree except itself.

    Deliberately opt-in: this reads every byte of the submission, which is the
    point when you are chasing a transfer problem and unacceptable otherwise.
    """
    root = Path(root)
    output = root / "checksums.sha256"
    lines = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == output:
            continue
        lines.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="the submission root directory")
    parser.add_argument("--submission-id")
    parser.add_argument("--organization")
    parser.add_argument("--contact")
    parser.add_argument("--proposal-id")
    parser.add_argument(
        "--checksums",
        action="store_true",
        help="also write checksums.sha256 over every file (reads the whole tree)",
    )
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="print the manifest instead of writing it",
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 1

    manifest = build_manifest(
        args.root,
        submission_id=args.submission_id,
        organization=args.organization,
        contact=args.contact,
        proposal_id=args.proposal_id,
    )

    if args.print_only:
        print(json.dumps(manifest, indent=2))
        return 0

    path = write_manifest(args.root, manifest)
    counts = manifest["counts"]
    print(
        f"wrote {path} -- {counts['patients']} patient(s), {counts['studies']} study(ies), "
        f"{counts['timepoints']} time point(s)"
    )
    if args.checksums:
        print(f"wrote {write_checksums(args.root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
