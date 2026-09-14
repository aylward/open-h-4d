# SPDX-License-Identifier: Apache-2.0
"""The authoritative layout checker, exercised against deliberate defects.

Each test copies a known-good synthetic submission, breaks it in exactly one
way, and asserts the specific error code. A verifier that only ever says yes has
not been tested.
"""

import json
import shutil

import numpy as np
import pytest

from openh4d.synthetic import generate
from openh4d.verify_layout import verify


@pytest.fixture(scope="module")
def pristine(tmp_path_factory):
    """One good submission, generated once and copied per test."""
    root = tmp_path_factory.mktemp("pristine")
    return generate(root / "submission", n_timepoints=4, shape=(8, 8, 4))


@pytest.fixture
def root(pristine, tmp_path):
    target = tmp_path / "submission"
    shutil.copytree(pristine, target)
    return target


def codes(report):
    return {finding["code"] for finding in report.errors}


def warn_codes(report):
    return {finding["code"] for finding in report.warnings}


def read_study(root, study_id):
    return json.loads((root / study_id / "study.json").read_text("utf-8"))


def write_study(root, study_id, study):
    (root / study_id / "study.json").write_text(json.dumps(study, indent=2), encoding="utf-8")


def read_patient(root, patient_id):
    return json.loads((root / f"{patient_id}_ehr" / "patient.json").read_text("utf-8"))


def write_patient(root, patient_id, patient):
    (root / f"{patient_id}_ehr" / "patient.json").write_text(
        json.dumps(patient, indent=2), encoding="utf-8"
    )


# --- the happy path ----------------------------------------------------------


def test_pristine_submission_is_compliant(root):
    report = verify(root)
    assert report.compliant, report.errors
    assert report.warnings == []
    assert report.summary["patients"] == 2
    assert report.summary["studies"] == 3
    assert report.summary["timepoints"] == 12


def test_verify_single_study(root):
    report = verify(root, only_study="OH4D-0001A")
    assert report.compliant, report.errors
    assert report.summary["studies"] == 1


def test_verify_unknown_study(root):
    assert "E_STUDY_NOT_FOUND" in codes(verify(root, only_study="OH4D-9999Z"))


def test_missing_root_is_reported_not_raised(tmp_path):
    report = verify(tmp_path / "nope")
    assert "E_ROOT_NOT_A_DIRECTORY" in codes(report)


# --- root-level --------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,code",
    [
        ("README.md", "E_MISSING_README"),
        ("LICENSE", "E_MISSING_LICENSE"),
        ("manifest.json", "E_MISSING_MANIFEST"),
    ],
)
def test_missing_root_file(root, filename, code):
    (root / filename).unlink()
    assert code in codes(verify(root))


def test_unexpected_root_file_is_a_warning_not_an_error(root):
    (root / "notes.txt").write_text("scratch", encoding="utf-8")
    report = verify(root)
    assert report.compliant, report.errors
    assert "W_UNEXPECTED_ROOT_ENTRY" in warn_codes(report)


def test_empty_root_reports_no_studies(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "E_NO_STUDIES" in codes(verify(empty))


def test_invalid_directory_name(root):
    (root / "not-a-study").mkdir()
    assert "E_INVALID_DIR_NAME" in codes(verify(root))


def test_bare_patient_id_directory_is_rejected(root):
    """A single-study patient still needs the 'A' suffix."""
    (root / "OH4D-0003").mkdir()
    report = verify(root)
    assert "E_INVALID_DIR_NAME" in codes(report)
    assert any("OH4D-0003A" in f["message"] for f in report.errors)


# --- PHI guards --------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["crosswalk.csv", "patient_mrn.csv", "phi-notes.txt", "id_linking.json", "CROSSWALK.CSV"],
)
def test_planted_crosswalk_is_an_error(root, filename):
    (root / filename).write_text("oh4d_patient_id,source_patient_id\n", encoding="utf-8")
    assert "E_FORBIDDEN_FILE" in codes(verify(root))


def test_crosswalk_found_deep_in_the_tree(root):
    (root / "OH4D-0001A" / "mrn_map.csv").write_text("x", encoding="utf-8")
    assert "E_FORBIDDEN_FILE" in codes(verify(root))


@pytest.mark.parametrize("filename", ["graphics.md", "morphine-log.txt", "alphabet.json"])
def test_ordinary_words_containing_phi_are_not_flagged(root, filename):
    """'graphics' contains the letters 'phi'; a substring match would be useless."""
    (root / filename).write_text("x", encoding="utf-8")
    assert "E_FORBIDDEN_FILE" not in codes(verify(root))


def test_leaked_date_in_patient_json(root):
    patient = read_patient(root, "OH4D-0001")
    patient["disease_state"]["notes"] = "Repaired 1962-04-17"
    write_patient(root, "OH4D-0001", patient)
    assert "E_DATE_IN_METADATA" in codes(verify(root))


# --- patient-level -----------------------------------------------------------


def test_study_suffix_gap(root):
    (root / "OH4D-0001B").rename(root / "OH4D-0001C")
    report = verify(root)
    assert "E_STUDY_SUFFIX_GAP" in codes(report)
    assert any("contiguous" in f["message"] for f in report.errors)


def test_missing_ehr_directory(root):
    shutil.rmtree(root / "OH4D-0001_ehr")
    assert "E_MISSING_EHR_DIR" in codes(verify(root))


def test_missing_patient_json(root):
    (root / "OH4D-0001_ehr" / "patient.json").unlink()
    assert "E_MISSING_PATIENT_JSON" in codes(verify(root))


def test_unreadable_patient_json(root):
    (root / "OH4D-0001_ehr" / "patient.json").write_text("{not json", encoding="utf-8")
    assert "E_PATIENT_JSON_UNREADABLE" in codes(verify(root))


def test_ehr_without_studies(root):
    (root / "OH4D-0009_ehr").mkdir()
    assert "E_EHR_WITHOUT_STUDIES" in codes(verify(root))


def test_patient_json_id_mismatch(root):
    patient = read_patient(root, "OH4D-0001")
    patient["patient_id"] = "OH4D-0007"
    write_patient(root, "OH4D-0001", patient)
    assert "E_PATIENT_ID_MISMATCH" in codes(verify(root))


def test_patient_json_lists_a_missing_study(root):
    patient = read_patient(root, "OH4D-0001")
    patient["studies"].append("OH4D-0001C")
    write_patient(root, "OH4D-0001", patient)
    assert "E_PATIENT_STUDIES_MISSING" in codes(verify(root))


def test_study_directory_not_listed_in_patient_json(root):
    patient = read_patient(root, "OH4D-0001")
    patient["studies"] = ["OH4D-0001A"]
    write_patient(root, "OH4D-0001", patient)
    assert "E_PATIENT_STUDIES_UNLISTED" in codes(verify(root))


# --- study-level -------------------------------------------------------------


def test_missing_study_json(root):
    (root / "OH4D-0001A" / "study.json").unlink()
    assert "E_MISSING_STUDY_JSON" in codes(verify(root))


def test_unreadable_study_json(root):
    (root / "OH4D-0001A" / "study.json").write_text("[]", encoding="utf-8")
    assert "E_STUDY_JSON_UNREADABLE" in codes(verify(root))


def test_study_id_mismatch(root):
    study = read_study(root, "OH4D-0001A")
    study["study_id"] = "OH4D-0001B"
    write_study(root, "OH4D-0001A", study)
    assert "E_STUDY_ID_MISMATCH" in codes(verify(root))


def test_timepoint_gap(root):
    (root / "OH4D-0001A" / "t0002.nii.gz").unlink()
    report = verify(root)
    assert "E_TIMEPOINT_GAP" in codes(report)


def test_timepoint_count_mismatch_against_disk(root):
    """Deleting the last time point leaves no gap, so the count is what catches it."""
    (root / "OH4D-0001A" / "t0003.nii.gz").unlink()
    study = read_study(root, "OH4D-0001A")
    study["timepoints"] = study["timepoints"][:3]
    study["n_timepoints"] = 3
    write_study(root, "OH4D-0001A", study)
    report = verify(root)
    assert report.compliant, report.errors

    study["n_timepoints"] = 4
    write_study(root, "OH4D-0001A", study)
    assert "E_TIMEPOINT_COUNT_MISMATCH" in codes(verify(root))


def test_no_image_data(root):
    for path in (root / "OH4D-0001A").glob("t*.nii.gz"):
        path.unlink()
    assert "E_NO_IMAGE_DATA" in codes(verify(root))


def test_mixed_layout_forms(root):
    """Per-time-point volumes plus a single 4D file is ambiguous."""
    shutil.copy(root / "OH4D-0001A" / "t0000.nii.gz", root / "OH4D-0001A" / "image4d.nii.gz")
    assert "E_MIXED_LAYOUT" in codes(verify(root))


def test_layout_mismatch_between_json_and_disk(root):
    study = read_study(root, "OH4D-0001A")
    study["file_layout"] = "dicom_phases"
    write_study(root, "OH4D-0001A", study)
    assert "E_LAYOUT_MISMATCH" in codes(verify(root))


def test_dicom_alongside_nifti_is_legal_when_declared(root):
    """organize followed by convert produces exactly this shape; it must not be flagged."""
    study_dir = root / "OH4D-0001A"
    for index in range(4):
        phase = study_dir / "dicom" / f"t{index:04d}"
        phase.mkdir(parents=True)
        (phase / "0001.dcm").write_bytes(b"not really dicom, but a file")

    study = read_study(root, "OH4D-0001A")
    study["file_layout"] = "dicom_phases"
    study["derived_from_dicom"] = True
    write_study(root, "OH4D-0001A", study)

    report = verify(root)
    assert report.compliant, report.errors


def test_empty_phase_directory(root):
    study_dir = root / "OH4D-0001A"
    for path in study_dir.glob("t*.nii.gz"):
        path.unlink()
    for index in range(4):
        (study_dir / "dicom" / f"t{index:04d}").mkdir(parents=True)
    (study_dir / "dicom" / "t0000" / "0001.dcm").write_bytes(b"x")

    study = read_study(root, "OH4D-0001A")
    study["file_layout"] = "dicom_phases"
    write_study(root, "OH4D-0001A", study)
    assert "E_EMPTY_PHASE_DIR" in codes(verify(root))


def test_single_4d_form_warns_but_passes(tmp_path):
    fallback = generate(tmp_path / "fallback", n_timepoints=4, shape=(8, 8, 4), form="single_4d")
    report = verify(fallback)
    assert report.compliant, report.errors
    assert "W_FALLBACK_LAYOUT" in warn_codes(report)


def test_single_4d_that_is_actually_3d(tmp_path):
    import nibabel as nib

    fallback = generate(tmp_path / "f", n_timepoints=4, shape=(8, 8, 4), form="single_4d")
    target = fallback / "OH4D-0001A" / "image4d.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((8, 8, 4), dtype=np.int16), np.eye(4)), str(target))
    assert "E_SINGLE_4D_NOT_4D" in codes(verify(fallback))


def test_unreadable_volume(root):
    (root / "OH4D-0001A" / "t0001.nii.gz").write_bytes(b"not a nifti")
    assert "E_VOLUME_UNREADABLE" in codes(verify(root))


# --- geometry ----------------------------------------------------------------


def test_declared_geometry_must_match_the_header(root):
    study = read_study(root, "OH4D-0001A")
    study["geometry"]["in_plane_mm"] = [1.5, 1.5]
    write_study(root, "OH4D-0001A", study)
    report = verify(root)
    assert "E_GEOMETRY_MISMATCH" in codes(report)


def test_declared_matrix_must_match_the_header(root):
    study = read_study(root, "OH4D-0001A")
    study["geometry"]["matrix"] = [64, 64, 64]
    write_study(root, "OH4D-0001A", study)
    assert "E_GEOMETRY_MISMATCH" in codes(verify(root))


def test_affine_must_be_consistent_across_time_points(root):
    import nibabel as nib

    target = root / "OH4D-0001A" / "t0002.nii.gz"
    image = nib.load(str(target))
    shifted = np.asarray(image.affine)
    shifted[0, 3] += 5.0
    nib.save(nib.Nifti1Image(np.asanyarray(image.dataobj), shifted), str(target))
    assert "E_AFFINE_INCONSISTENT" in codes(verify(root))


def test_shape_must_be_consistent_across_time_points(root):
    import nibabel as nib

    target = root / "OH4D-0001A" / "t0002.nii.gz"
    affine = np.asarray(nib.load(str(target)).affine)
    nib.save(nib.Nifti1Image(np.zeros((8, 8, 5), dtype=np.int16), affine), str(target))
    assert "E_SHAPE_INCONSISTENT" in codes(verify(root))


# --- RFP conformance ---------------------------------------------------------


def test_ct_slice_spacing_over_the_cardiac_limit(root):
    """The synthetic fixture is a cardiac CT, so the 2 mm limit applies."""
    study = read_study(root, "OH4D-0001A")
    study["geometry"]["slice_spacing_mm"] = 3.0
    write_study(root, "OH4D-0001A", study)
    report = verify(root)
    assert "E_CONFORMANCE" in codes(report)
    assert any("ct_slice_spacing_cardiac" in f["message"] for f in report.errors)


def test_a_waiver_downgrades_a_conformance_error_to_a_warning(root):
    study = read_study(root, "OH4D-0001A")
    study["geometry"]["slice_spacing_mm"] = 3.0
    study["waivers"] = [
        {
            "rule": "ct_slice_spacing_cardiac",
            "justification": "legacy scanner; thinner reconstruction is not available",
        }
    ]
    write_study(root, "OH4D-0001A", study)
    report = verify(root)
    # The geometry mismatch against the header is a separate, real error.
    assert "E_CONFORMANCE" not in codes(report)
    assert "W_CONFORMANCE" in warn_codes(report)


def test_unknown_waiver_is_an_error(root):
    study = read_study(root, "OH4D-0001A")
    study["waivers"] = [{"rule": "ct_slice_spacing_spleen", "justification": "typo"}]
    write_study(root, "OH4D-0001A", study)
    assert "E_UNKNOWN_WAIVER" in codes(verify(root))


def test_min_timepoints_cannot_be_waived(root):
    study = read_study(root, "OH4D-0001A")
    study["waivers"] = [{"rule": "min_timepoints", "justification": "only got one phase"}]
    write_study(root, "OH4D-0001A", study)
    assert "E_UNWAIVABLE_WAIVER" in codes(verify(root))


def test_ct_low_dose_must_be_declared(root):
    study = read_study(root, "OH4D-0001A")
    study["ct"]["low_dose"] = None
    write_study(root, "OH4D-0001A", study)
    report = verify(root)
    assert any("ct_low_dose_declared" in f["message"] for f in report.errors)


# --- derived products --------------------------------------------------------


def test_undeclared_derived_product(root):
    derived = root / "OH4D-0001A" / "derived"
    derived.mkdir()
    (derived / "motion_field.nii.gz").write_bytes(b"x")
    assert "E_UNDECLARED_DERIVED" in codes(verify(root))


def test_declared_derived_product_passes(root):
    derived = root / "OH4D-0001A" / "derived"
    derived.mkdir()
    (derived / "motion_field.nii.gz").write_bytes(b"x")
    study = read_study(root, "OH4D-0001A")
    study["derived"] = [
        {"path": "derived/motion_field.nii.gz", "description": "displacement field, t0 to t2"}
    ]
    write_study(root, "OH4D-0001A", study)
    report = verify(root)
    assert report.compliant, report.errors
