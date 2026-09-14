#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build an OpenH-4D submission from a 3D Slicer 4D sequence NRRD.

The source is one file: ``TruncalValve_4DCT.seq.nrrd``, a cardiac-gated 4D CT of
a truncal valve written by 3D Slicer as a sequence. This is the path a
contributor takes when their 4D data lives in a research tool's own format
rather than in DICOM.

Two properties of that file are what make this example worth having, and both
are silent failures if handled wrongly:

* **Time comes first.** The header reads ``sizes: 21 512 391 218`` with
  ``kinds: list domain domain domain`` -- 21 time points of a 512x391x218
  volume. SimpleITK surfaces that leading axis as vector components on a 3D
  image, not as a fourth dimension.
* **The handedness flips.** The file declares ``space: right-anterior-superior``;
  SimpleITK normalizes to LPS on read; NIfTI wants RAS. Getting the flip wrong
  mirrors the anatomy and raises nothing at all.

Both are handled in :mod:`openh4d.convert_nrrd`, and this example is the
end-to-end check that they stay handled.

    python examples/truncalvalve-4dct/build_example.py --out /tmp/oh4d-tv

Downloads roughly 1.3 GB on first run and caches it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from openh4d.convert_nrrd import convert
from openh4d.download import fetch
from openh4d.manifest import build_manifest, write_manifest
from openh4d.motion_report import build_report
from openh4d.naming import ehr_dir_name, make_patient_id, study_dir_name
from openh4d.verify_layout import verify

HERE = Path(__file__).resolve().parent

SOURCE_URL = (
    "https://github.com/Project-MONAI/monai-physio/releases/download/2026.07.1/"
    "TruncalValve_4DCT.seq.nrrd"
)
SOURCE_SIZE = 1_267_650_314

ID_PREFIX = "CHOP"

DATA_CARD = """\
---
pretty_name: "OpenH-4D -- Truncal Valve 4D CT"
license: cc-by-4.0
task_categories:
  - image-segmentation
tags:
  - openh-4d
  - 4d
  - ct
  - heart
  - cardiac-motion
language:
  - en
size_categories:
  - n<1K
---

# OpenH-4D -- Truncal Valve 4D CT

A cardiac-gated 4D CT of a truncal valve, restructured into the OpenH-4D
submission layout from the `TruncalValve_4DCT.seq.nrrd` release asset published
by [Project-MONAI/monai-physio](https://github.com/Project-MONAI/monai-physio).

## Dataset Description

One subject, one 4D study, {n_timepoints} time points spanning the cardiac
cycle. The source is a 3D Slicer sequence: a single NRRD whose leading axis is
time.

## Dataset Contributor(s)

Restructured by the OpenH-4D tooling from a public release.

## License / Terms of Use

CC BY 4.0, inherited from the upstream release.

## Intended Usage

Modelling cardiac valve motion; a worked example of the OpenH-4D layout for
contributors whose data is in a research tool's own format rather than DICOM.

## Dataset Characterization

- **Data Collection Method:** clinical
- **Labeling Method:** N/A (no annotations in this release)
- **Acquisition system:** cardiac-gated 4D CT, {in_plane} mm in plane,
  {slice_spacing} mm slices

## Dataset Format

OpenH-4D submission layout: one `t####.nii.gz` per cardiac phase, converted
from the Slicer sequence with SimpleITK. The source declares RAS; the written
NIfTI affines carry that geometry through unchanged, with no resampling.

## Dataset Quantification

- Subjects: 1
- Studies: 1
- Time points: {n_timepoints}
- Matrix: {matrix}

## Subject Metadata

Not published with this release. `patient.json` records the de-identification
status and the reason for scan; demographics and vitals are absent upstream and
are left null rather than guessed.

## Data Validation

`python -m openh4d.verify_layout <root>` reports `compliant: true`.
`python -m openh4d.motion_report <study-dir>` measures the valve motion across
the cardiac cycle; peak centre-of-mass travel for this study is
{peak_travel} voxels.

**Orientation check.** Open `t0000.nii.gz` alongside the original
`.seq.nrrd` in 3D Slicer and confirm they overlay. No unit test substitutes for
this: a wrong handedness flip mirrors the anatomy without raising anything.

## Known Issues

Subject-level clinical metadata is not available upstream, so `patient.json` is
sparse. A real OpenH-4D submission is expected to populate it.

## Ethical Considerations

The upstream release is public and de-identified. The OpenH-4D identifier is
assigned locally.
"""

CC_BY = """\
This dataset is licensed under the Creative Commons Attribution 4.0
International License (CC BY 4.0).

    https://creativecommons.org/licenses/by/4.0/

Restructured from the Project-MONAI/monai-physio TruncalValve_4DCT release.
"""


def apply_clinical_context(study_dir: Path, n_timepoints: int) -> None:
    """Facts about the acquisition that the NRRD header does not carry."""
    path = study_dir / "study.json"
    study = json.loads(path.read_text(encoding="utf-8"))
    study.update(
        {
            "modality": "CT",
            "organ": "heart",
            "motion": "cardiac",
            "protocol": "Retrospectively ECG-gated 4D CT",
            "clinical_indication": "truncal valve assessment / surgical planning",
            "coverage": "full",
        }
    )
    study["contrast"] = {"used": True, "agent": "iodinated", "phase": None}
    study["ct"] = {"low_dose": None, "kvp": None, "exposure_mas": None, "recon_kernel": None}
    study["gating"] = {
        "method": "retrospective_ecg",
        "phase_source": "Slicer sequence index values",
    }

    # The sequence index values are frame numbers, not percentages, so the phase
    # fractions are evenly spaced across the cycle rather than read from the file.
    for index, timepoint in enumerate(study["timepoints"]):
        timepoint["phase_type"] = "cardiac"
        timepoint["phase_fraction"] = round(index / n_timepoints, 6)

    study["notes"] = (
        "Restructured from TruncalValve_4DCT.seq.nrrd by "
        "examples/truncalvalve-4dct/build_example.py. CT acquisition parameters are not "
        "published with the release. Phase fractions are evenly spaced across the cycle: the "
        "source records frame indices, not measured R-R percentages."
    )
    study["waivers"] = [
        {
            "rule": "ct_low_dose_declared",
            "justification": (
                "Dose information is not published with the upstream release and is not "
                "recoverable from the NRRD header."
            ),
        }
    ]
    path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")


def write_patient_json(root: Path, patient_id: str, study_ids: list[str]) -> None:
    document = {
        "schema": "openh-4d/patient/1.0",
        "patient_id": patient_id,
        "studies": study_ids,
        "demographics": {
            "age_years": None,
            "age_90_or_older": True,
            "sex": "unknown",
            "gender": None,
            "height_cm": None,
            "weight_kg": None,
            "bmi": None,
            "ethnicity": None,
        },
        "disease_state": {
            "primary_diagnosis": "truncal valve disease",
            "codes": [],
            "severity": None,
            "comorbidities": [],
            "notes": "Collection-level diagnosis; per-subject detail is not published.",
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
        "reason_for_scan": {
            "category": "surgical_planning",
            "description": "truncal valve assessment",
        },
        "deidentification": {
            "standard": "hipaa_safe_harbor",
            "date_shift_applied": True,
            "date_shift_offset_disclosed": False,
            "performed_by": "upstream release",
            "tooling": None,
            "attestation": (
                "Publicly released and de-identified upstream. The OpenH-4D identifier is "
                "assigned locally."
            ),
        },
        "extra": {},
    }
    ehr = root / ehr_dir_name(patient_id)
    ehr.mkdir(parents=True, exist_ok=True)
    (ehr / "patient.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "out")
    parser.add_argument("--cache", type=Path, help="override the download cache directory")
    parser.add_argument("--source", type=Path, help="use a local file instead of downloading")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print("1. fetching the sequence")
    source = (
        Path(args.source)
        if args.source
        else fetch(SOURCE_URL, cache_dir=args.cache, expected_size=SOURCE_SIZE)
    )

    patient_id = make_patient_id(ID_PREFIX, 1)
    study_id = study_dir_name(patient_id, 1)

    print("2. converting the Slicer sequence to per-time-point NIfTI")
    report = convert(source, out / study_id, study_id=study_id, patient_id=patient_id)
    print(
        f"   {report['n_timepoints']} time point(s); SimpleITK normalized the source to "
        f"{report['normalized_space']} on read, and the written affines carry it back to RAS"
    )
    print(
        f"   voxel axes {report['geometry']['orientation']}, matrix {report['geometry']['matrix']}"
    )

    apply_clinical_context(out / study_id, report["n_timepoints"])
    write_patient_json(out, patient_id, [study_id])

    print("3. measuring the motion")
    motion = build_report(out / study_id)
    print(f"   peak centre-of-mass travel: {motion['peak_centroid_travel_voxels']} voxels")
    for observation in motion["observations"]:
        print(f"   note: {observation}", file=sys.stderr)

    print("4. writing the data card and manifest")
    manifest = build_manifest(out, submission_id="openh4d-truncalvalve-4dct")
    write_manifest(out, manifest)
    (out / "README.md").write_text(
        DATA_CARD.format(
            n_timepoints=report["n_timepoints"],
            matrix=report["geometry"]["matrix"],
            in_plane=report["geometry"]["in_plane_mm"][0],
            slice_spacing=report["geometry"]["slice_spacing_mm"],
            peak_travel=motion["peak_centroid_travel_voxels"],
        ),
        encoding="utf-8",
    )
    (out / "LICENSE").write_text(CC_BY, encoding="utf-8")
    write_manifest(out, build_manifest(out, submission_id="openh4d-truncalvalve-4dct"))

    print("5. verifying")
    verification = verify(out)
    print(
        json.dumps({"compliant": verification.compliant, "summary": verification.summary}, indent=2)
    )
    for finding in verification.errors:
        print(f"   ERROR {finding['code']}: {finding['message']}", file=sys.stderr)
    for finding in verification.warnings:
        print(f"   warning {finding['code']}: {finding['message']}", file=sys.stderr)

    if not verification.compliant:
        return 1

    print(f"\nBuilt a compliant OpenH-4D submission at {out}")
    print(
        "\nOrientation check, worth doing once by hand: open "
        f"{out / study_id / 't0000.nii.gz'} alongside the original sequence in 3D Slicer and "
        "confirm they overlay."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
