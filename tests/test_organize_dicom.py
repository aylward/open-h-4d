# SPDX-License-Identifier: Apache-2.0
"""Applying a DICOM index plan to produce the submission layout.

The three things worth being careful about are identifier assignment (it must be
reproducible, because a published study letter is part of a citable identifier),
the crosswalk (it must never end up inside the submission), and tag retention
(only allowlisted tags survive the copy).
"""

import json

import pytest

from openh4d import naming
from openh4d.dicom_index import build_plan
from openh4d.organize_dicom import (
    OrganizeError,
    assign_identifiers,
    load_allowlist,
    organize,
    resolve_crosswalk_path,
)
from openh4d.synthetic_dicom import write_multi_patient_source, write_series

SMALL = (8, 8, 4)


@pytest.fixture(scope="module")
def plan(tmp_path_factory):
    source = write_series(tmp_path_factory.mktemp("src") / "dicom", n_timepoints=4, shape=SMALL)
    return build_plan(source)


@pytest.fixture(scope="module")
def multi_plan(tmp_path_factory):
    source = write_multi_patient_source(
        tmp_path_factory.mktemp("multi") / "dicom", n_patients=2, n_timepoints=2, shape=SMALL
    )
    return build_plan(source)


@pytest.fixture
def crosswalk(tmp_path):
    return tmp_path / "outside" / "crosswalk.csv"


# --- identifier assignment ---------------------------------------------------


def test_assignment_uses_the_issued_prefix(plan):
    assignments = assign_identifiers(plan, "STAN")
    assert assignments[0].patient_id == "STAN-0001"
    assert assignments[0].study_id == "STAN-0001A"


def test_single_study_patient_still_gets_the_a_suffix(plan):
    """One rule, no special case, so adding a second study never renames the first."""
    assert assign_identifiers(plan, "STAN")[0].study_id.endswith("A")


def test_second_study_gets_b(multi_plan):
    assignments = assign_identifiers(multi_plan, "DUKE")
    by_patient = {}
    for assignment in assignments:
        by_patient.setdefault(assignment.patient_id, []).append(assignment.study_id)
    two_study_patient = next(v for v in by_patient.values() if len(v) == 2)
    assert sorted(two_study_patient) == ["DUKE-0001A", "DUKE-0001B"]


def test_assignment_is_reproducible(multi_plan):
    first = [(a.patient_id, a.study_id) for a in assign_identifiers(multi_plan, "STAN")]
    second = [(a.patient_id, a.study_id) for a in assign_identifiers(multi_plan, "STAN")]
    assert first == second


def test_id_start_lets_the_steering_group_hand_out_disjoint_ranges(plan):
    assert assign_identifiers(plan, "STAN", start=500)[0].patient_id == "STAN-0500"


def test_invalid_prefix_is_rejected_before_anything_is_written(plan):
    with pytest.raises(naming.InvalidNameError):
        assign_identifiers(plan, "stan")


# --- the crosswalk -----------------------------------------------------------


def test_crosswalk_inside_the_submission_is_refused(tmp_path):
    root = tmp_path / "submission"
    with pytest.raises(OrganizeError, match="never travel with the data"):
        resolve_crosswalk_path(root, root / "crosswalk.csv", "x")


def test_crosswalk_at_the_submission_root_is_refused(tmp_path):
    root = tmp_path / "submission"
    with pytest.raises(OrganizeError):
        resolve_crosswalk_path(root, root, "x")


def test_crosswalk_defaults_outside_the_submission(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENH4D_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "submission"
    path = resolve_crosswalk_path(root, None, "example")
    assert root.resolve() not in path.parents


def test_crosswalk_records_the_mapping(plan, tmp_path, crosswalk):
    organize(plan, tmp_path / "out", prefix="STAN", crosswalk=crosswalk, apply=True)
    rows = crosswalk.read_text(encoding="utf-8").strip().splitlines()
    assert rows[0].startswith("oh4d_patient_id,oh4d_study_id,source_patient_id")
    assert "STAN-0001A" in rows[1]
    assert "SRC-PATIENT-001" in rows[1]


def test_crosswalk_is_not_written_on_a_dry_run(plan, tmp_path, crosswalk):
    organize(plan, tmp_path / "out", prefix="STAN", crosswalk=crosswalk, apply=False)
    assert not crosswalk.exists()


# --- dry run vs apply --------------------------------------------------------


def test_dry_run_writes_nothing(plan, tmp_path, crosswalk):
    out = tmp_path / "out"
    report = organize(plan, out, prefix="STAN", crosswalk=crosswalk, apply=False)
    assert report["applied"] is False
    assert report["n_studies"] == 1
    assert not out.exists()


def test_apply_writes_the_expected_tree(plan, tmp_path, crosswalk):
    out = tmp_path / "out"
    organize(plan, out, prefix="STAN", crosswalk=crosswalk, apply=True)

    assert (out / "STAN-0001A" / "study.json").is_file()
    assert (out / "STAN-0001_ehr" / "patient.json").is_file()
    for index in range(4):
        phase = out / "STAN-0001A" / "dicom" / f"t{index:04d}"
        assert phase.is_dir()
        assert len(list(phase.glob("*.dcm"))) == 4


def test_study_stub_carries_what_the_headers_gave(plan, tmp_path, crosswalk):
    out = tmp_path / "out"
    organize(plan, out, prefix="STAN", crosswalk=crosswalk, apply=True)
    study = json.loads((out / "STAN-0001A" / "study.json").read_text("utf-8"))

    assert study["study_id"] == "STAN-0001A"
    assert study["patient_id"] == "STAN-0001"
    assert study["modality"] == "CT"
    assert study["n_timepoints"] == 4
    assert study["geometry"]["in_plane_mm"] == [0.8, 0.8]
    assert study["gating"]["phase_source"] == "SeriesDescription"


def test_study_stub_leaves_contributor_fields_null(plan, tmp_path, crosswalk):
    """organ, motion and coverage are not in any DICOM header and must not be invented."""
    out = tmp_path / "out"
    organize(plan, out, prefix="STAN", crosswalk=crosswalk, apply=True)
    study = json.loads((out / "STAN-0001A" / "study.json").read_text("utf-8"))

    assert study["organ"] is None
    assert study["motion"] is None
    assert study["coverage"] is None
    assert study["contrast"]["used"] is None


def test_the_stub_does_not_verify_until_a_contributor_fills_it_in(plan, tmp_path, crosswalk):
    """The null placeholders must be rejected, or an incomplete submission would pass."""
    from openh4d.verify_layout import verify

    out = tmp_path / "out"
    organize(plan, out, prefix="STAN", crosswalk=crosswalk, apply=True)
    report = verify(out)
    assert not report.compliant
    messages = " ".join(f["message"] for f in report.errors)
    assert "organ" in messages and "motion" in messages


def test_patient_stub_lists_its_studies(multi_plan, tmp_path, crosswalk):
    out = tmp_path / "out"
    organize(multi_plan, out, prefix="STAN", crosswalk=crosswalk, apply=True)
    patient = json.loads((out / "STAN-0001_ehr" / "patient.json").read_text("utf-8"))
    assert patient["studies"] == ["STAN-0001A", "STAN-0001B"]


def test_no_keep_dicom_skips_the_copy(plan, tmp_path, crosswalk):
    out = tmp_path / "out"
    organize(plan, out, prefix="STAN", crosswalk=crosswalk, keep_dicom=False, apply=True)
    assert not (out / "STAN-0001A" / "dicom").exists()
    study = json.loads((out / "STAN-0001A" / "study.json").read_text("utf-8"))
    assert study["file_layout"] == "timepoint_volumes"


# --- tag allowlist -----------------------------------------------------------


def test_allowlist_parses_into_keywords():
    keywords = load_allowlist()
    assert "ImagePositionPatient" in keywords
    assert "PixelSpacing" in keywords
    assert "TemporalPositionIdentifier" in keywords


def test_allowlist_excludes_the_safe_harbor_identifiers():
    keywords = load_allowlist()
    for identifier in (
        "PatientName",
        "PatientBirthDate",
        "AccessionNumber",
        "InstitutionName",
        "ReferringPhysicianName",
        "StudyDate",
        "DeviceSerialNumber",
    ):
        assert identifier not in keywords, f"{identifier} must not survive the copy"


def test_retained_dicom_is_stripped_to_the_allowlist(tmp_path, crosswalk):
    """Defense in depth: identified tags must not survive even if the source had them."""
    import pydicom

    source = write_series(tmp_path / "src", n_timepoints=2, shape=SMALL, include_phi=True)
    out = tmp_path / "out"
    organize(build_plan(source), out, prefix="STAN", crosswalk=crosswalk, apply=True)

    copied = sorted((out / "STAN-0001A" / "dicom").rglob("*.dcm"))
    assert copied
    dataset = pydicom.dcmread(str(copied[0]))
    for identifier in (
        "PatientName",
        "PatientBirthDate",
        "StudyDate",
        "InstitutionName",
        "ReferringPhysicianName",
        "AccessionNumber",
    ):
        assert identifier not in dataset, f"{identifier} survived the allowlist"

    # Geometry and phase information must survive, or conversion breaks.
    assert dataset.PixelSpacing == [0.8, 0.8]
    assert dataset.ImageOrientationPatient == [1, 0, 0, 0, 1, 0]
    assert dataset.PixelData


def test_patient_id_is_rewritten_to_the_openh4d_identifier(tmp_path, crosswalk):
    import pydicom

    source = write_series(tmp_path / "src", n_timepoints=2, shape=SMALL)
    out = tmp_path / "out"
    organize(build_plan(source), out, prefix="STAN", crosswalk=crosswalk, apply=True)

    copied = sorted((out / "STAN-0001A" / "dicom").rglob("*.dcm"))[0]
    assert pydicom.dcmread(str(copied)).PatientID == "STAN-0001"


def test_no_source_patient_id_survives_anywhere_in_the_tree(tmp_path, crosswalk):
    source = write_series(tmp_path / "src", n_timepoints=2, shape=SMALL)
    out = tmp_path / "out"
    organize(build_plan(source), out, prefix="STAN", crosswalk=crosswalk, apply=True)

    needle = b"SRC-PATIENT-001"
    for path in out.rglob("*"):
        if path.is_file():
            assert needle not in path.read_bytes(), f"source PatientID leaked into {path}"


# --- unrecoverable studies ---------------------------------------------------


def test_a_study_with_no_recoverable_time_points_is_blocked_not_guessed(
    tmp_path, crosswalk, monkeypatch
):
    from openh4d import dicom_index

    source = write_series(tmp_path / "src", encoding="none", n_timepoints=2, shape=SMALL)
    monkeypatch.setattr(dicom_index, "LADDER", ())
    plan = build_plan(source)

    report = organize(plan, tmp_path / "out", prefix="STAN", crosswalk=crosswalk, apply=True)
    assert report["n_studies"] == 0
    assert report["blocked"]
    assert not (tmp_path / "out" / "STAN-0001A").exists()
