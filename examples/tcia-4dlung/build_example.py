#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build an OpenH-4D submission from the TCIA 4D-Lung release.

**This archive is already NIfTI, not DICOM.** It holds eight subjects, each a
directory of ten respiratory-gated 3D volumes named ``<subject>_g000.nii.gz``
through ``<subject>_g090.nii.gz`` -- the 0% through 90% gates of one breathing
cycle. So this example covers the path a contributor takes when their data
never went through a PACS, or came back from a research pipeline as NIfTI:
re-identify, restructure, and describe. It does not exercise dcm2niix; the
synthetic example does that.

The interesting work here is the phase labels. ``g040`` means the 40% gate, and
that fraction is the only record of where in the breathing cycle each volume
sits. Dropping it would leave ten volumes in an arbitrary order.

    python examples/tcia-4dlung/build_example.py --out /tmp/oh4d-lung --max-patients 1

Downloads roughly 2 GB on first run and caches it, so re-runs are fast.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from openh4d.convert_nrrd import convert
from openh4d.download import extract_zip, fetch
from openh4d.manifest import build_manifest, write_manifest
from openh4d.naming import ehr_dir_name, make_patient_id, study_dir_name
from openh4d.paths import examples_dir
from openh4d.verify_layout import verify

HERE = Path(__file__).resolve().parent

ARCHIVE_URL = (
    "https://github.com/Project-MONAI/monai-physio/releases/download/2026.07.1/"
    "TCIA-4DLung-Part1.zip"
)
ARCHIVE_SIZE = 1_988_502_586

ID_PREFIX = "TCIA"

#: Filenames end in the respiratory gate, e.g. ``100_HM10395_g040.nii.gz`` = 40%.
GATE_RE = re.compile(r"_g(\d{3})$")

SOURCE_CITATION = (
    "Hugo, G. D., Weiss, E., Sleeman, W. C., Balik, S., Keall, P. J., Lu, J., & Williamson, "
    "J. F. (2016). Data from 4D Lung Imaging of NSCLC Patients. The Cancer Imaging Archive."
)

DATA_CARD = """\
---
pretty_name: "OpenH-4D -- TCIA 4D-Lung (Part 1)"
license: cc-by-4.0
task_categories:
  - image-segmentation
tags:
  - openh-4d
  - 4d
  - ct
  - lung
  - respiratory-motion
language:
  - en
size_categories:
  - n<1K
---

# OpenH-4D -- TCIA 4D-Lung (Part 1)

Respiratory-gated 4D CT of the thorax, restructured into the OpenH-4D
submission layout from the `TCIA-4DLung-Part1.zip` release asset published by
[Project-MONAI/monai-physio](https://github.com/Project-MONAI/monai-physio).

## Dataset Description

{n_patients} subject(s), {n_studies} 4D study(ies), {n_timepoints} time points in
total. Each study is one breathing cycle sampled at ten evenly spaced
respiratory gates, 0% through 90%, where 0% is end-inhalation.

The source data are lung cancer patients imaged for radiotherapy treatment
planning, which is why the acquisitions are 4D: the treatment plan has to
account for tumour motion with respiration.

## Dataset Contributor(s)

Restructured by the OpenH-4D tooling from a public release. Original
acquisition: see the citation below.

## Dataset Creation Date

See the upstream TCIA collection; the OpenH-4D restructuring adds no dates.

## License / Terms of Use

CC BY 4.0, inherited from the upstream TCIA collection.

## Intended Usage

Training and validating models of respiratory motion; a worked example of the
OpenH-4D layout for contributors whose data is already NIfTI.

## Dataset Characterization

- **Data Collection Method:** clinical
- **Labeling Method:** N/A (no annotations in this release)
- **Acquisition system:** 4D CT, respiratory-gated

## Dataset Format

OpenH-4D submission layout: one 4D study per directory, one `t####.nii.gz` per
respiratory gate, with the source gate recorded as each time point's
`phase_label` and `phase_fraction`.

## Dataset Quantification

- Subjects: {n_patients}
- Studies: {n_studies}
- Time points: {n_timepoints}

## Subject Metadata

Not published with this release. `patient.json` records only what is known:
the de-identification status and the reason for scan. Age, sex, disease state
and vitals are absent upstream and are left null rather than guessed.

## Data Validation

`python -m openh4d.verify_layout <root>` reports `compliant: true`.
`python -m openh4d.motion_report <study-dir>` shows diaphragm motion across the
ten gates.

## Known Issues

Subject-level clinical metadata is not available, so the `patient.json` files
are sparse. A real OpenH-4D submission is expected to populate them.

## Ethical Considerations

The upstream collection is publicly released and de-identified. The OpenH-4D
identifiers (`{prefix}-NNNN`) are assigned locally and do not encode the
upstream subject identifiers.

## Citation

{citation}
"""

CC_BY = """\
This dataset is licensed under the Creative Commons Attribution 4.0
International License (CC BY 4.0).

    https://creativecommons.org/licenses/by/4.0/

Restructured from the TCIA 4D-Lung collection. See README.md for the citation.
"""


def gate_fraction(stem: str) -> tuple[float | None, str | None]:
    """``100_HM10395_g040`` -> ``(0.4, "40%")``."""
    match = GATE_RE.search(stem)
    if not match:
        return None, None
    percent = int(match.group(1))
    return percent / 100.0, f"{percent}%"


def apply_phase_labels(study_dir: Path, source_labels: list[str]) -> None:
    """Record where in the breathing cycle each volume sits.

    The converter numbers the volumes t0000 onward; this restores the meaning
    those numbers stand for, which is the only thing making them a cycle rather
    than a sequence.
    """
    path = study_dir / "study.json"
    study = json.loads(path.read_text(encoding="utf-8"))
    for timepoint, source_label in zip(study["timepoints"], source_labels):
        fraction, label = gate_fraction(source_label)
        timepoint["phase_label"] = label or source_label
        timepoint["phase_fraction"] = fraction
        timepoint["phase_type"] = "respiratory"
    path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")


def apply_clinical_context(study_dir: Path) -> None:
    """Facts about the collection that no file header carries."""
    path = study_dir / "study.json"
    study = json.loads(path.read_text(encoding="utf-8"))
    study.update(
        {
            "modality": "CT",
            "organ": "lung",
            "motion": "respiratory",
            "protocol": "Respiratory-gated 4D CT, 10 phases",
            "clinical_indication": "lung cancer radiotherapy treatment planning",
            "coverage": "full",
        }
    )
    study["contrast"] = {"used": False, "agent": None, "phase": None}
    study["ct"] = {
        "low_dose": None,
        "kvp": None,
        "exposure_mas": None,
        "recon_kernel": None,
    }
    study["gating"] = {"method": "respiratory_belt", "phase_source": "filename gate index"}
    study["notes"] = (
        "Restructured from TCIA-4DLung-Part1.zip by examples/tcia-4dlung/build_example.py. "
        "CT acquisition parameters are not published with the release, so they are null; a "
        "contributor submitting this data for real would supply them."
    )
    # The RFP asks CT contributions to declare low dose; it is genuinely unknown
    # here, so the gap is recorded as a waiver rather than answered with a guess.
    study["waivers"] = [
        {
            "rule": "ct_low_dose_declared",
            "justification": (
                "Dose information is not published with the upstream release and is not "
                "recoverable from the NIfTI files."
            ),
        }
    ]
    path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")


def write_patient_json(root: Path, patient_id: str, study_ids: list[str]) -> None:
    """Sparse by necessity: the upstream release publishes no subject metadata."""
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
            "primary_diagnosis": "non-small-cell lung cancer",
            "codes": [],
            "severity": None,
            "comorbidities": [],
            "notes": "Collection-level diagnosis; per-subject staging is not published.",
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
            "category": "treatment_planning",
            "description": "radiotherapy planning for lung cancer",
        },
        "deidentification": {
            "standard": "hipaa_safe_harbor",
            "date_shift_applied": True,
            "date_shift_offset_disclosed": False,
            "performed_by": "upstream collection",
            "tooling": "TCIA de-identification pipeline",
            "attestation": (
                "Publicly released, de-identified upstream. OpenH-4D identifiers are assigned "
                "locally and do not encode the upstream subject identifier."
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
    parser.add_argument(
        "--max-patients",
        type=int,
        default=0,
        help="stop after this many subjects (0 = all 8); keeps scheduled CI runs bounded",
    )
    parser.add_argument("--cache", type=Path, help="override the download cache directory")
    args = parser.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print("1. fetching the archive")
    archive = fetch(ARCHIVE_URL, cache_dir=args.cache, expected_size=ARCHIVE_SIZE)
    extracted = extract_zip(archive, (args.cache or examples_dir()) / "TCIA-4DLung-Part1")

    subject_dirs = sorted(
        d for d in extracted.iterdir() if d.is_dir() and not d.name.startswith(".")
    )
    if args.max_patients:
        subject_dirs = subject_dirs[: args.max_patients]
    if not subject_dirs:
        print(f"no subject directories found under {extracted}", file=sys.stderr)
        return 1
    print(f"   {len(subject_dirs)} subject(s): {[d.name for d in subject_dirs]}")

    print("2. restructuring into the OpenH-4D layout")
    crosswalk_rows = []
    for index, subject_dir in enumerate(subject_dirs, start=1):
        patient_id = make_patient_id(ID_PREFIX, index)
        study_id = study_dir_name(patient_id, 1)

        report = convert(subject_dir, out / study_id, study_id=study_id, patient_id=patient_id)
        apply_phase_labels(out / study_id, report["phase_labels"] or [])
        apply_clinical_context(out / study_id)
        write_patient_json(out, patient_id, [study_id])

        crosswalk_rows.append((patient_id, study_id, subject_dir.name))
        print(f"   {subject_dir.name} -> {study_id} ({report['n_timepoints']} time points)")

    # The upstream identifiers are public, but the mapping still lives outside
    # the submission: the rule is the rule, and a future run against private
    # data must not depend on remembering to move it.
    crosswalk = (args.cache or examples_dir()) / "tcia-4dlung-crosswalk.csv"
    crosswalk.write_text(
        "oh4d_patient_id,oh4d_study_id,source_subject\n"
        + "\n".join(",".join(row) for row in crosswalk_rows)
        + "\n",
        encoding="utf-8",
    )
    print(f"   crosswalk written outside the submission: {crosswalk}")

    print("3. writing the data card and manifest")
    manifest = build_manifest(out, submission_id="openh4d-tcia-4dlung-part1")
    write_manifest(out, manifest)
    (out / "README.md").write_text(
        DATA_CARD.format(
            n_patients=manifest["counts"]["patients"],
            n_studies=manifest["counts"]["studies"],
            n_timepoints=manifest["counts"]["timepoints"],
            prefix=ID_PREFIX,
            citation=SOURCE_CITATION,
        ),
        encoding="utf-8",
    )
    (out / "LICENSE").write_text(CC_BY, encoding="utf-8")
    write_manifest(out, build_manifest(out, submission_id="openh4d-tcia-4dlung-part1"))

    print("4. verifying")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
