# SPDX-License-Identifier: Apache-2.0
"""Document-level validation of the JSON sidecars.

Each test mutates a known-good template in exactly one way and asserts the
matching finding appears. A validator that only ever says yes has not been
tested.
"""

import copy
import json

import pytest

from openh4d.schema import (
    validate_manifest,
    validate_patient,
    validate_study,
)

SHARED = "skills/openh-4d-shared"


def _codes(findings):
    return {f.code for f in findings}


@pytest.fixture
def patient(repo_root):
    return json.loads((repo_root / SHARED / "patient-json-template.json").read_text("utf-8"))


@pytest.fixture
def study(repo_root):
    return json.loads((repo_root / SHARED / "study-json-template.json").read_text("utf-8"))


@pytest.fixture
def manifest():
    return {
        "schema": "openh-4d/manifest/1.0",
        "submission_id": "example",
        "generated_by": "openh4d 0.1.0",
        "generated_at": "2027-03-04T12:00:00Z",
        "counts": {"patients": 1, "studies": 1, "timepoints": 10, "bytes": 1024},
        "modality_breakdown": {"CT": 1},
        "organ_breakdown": {"heart": 1},
        "patients": [
            {
                "patient_id": "STAN-0001",
                "ehr_dir": "STAN-0001_ehr",
                "patient_json_sha256": "0" * 64,
                "studies": [
                    {
                        "study_id": "STAN-0001A",
                        "modality": "CT",
                        "organ": "heart",
                        "file_layout": "timepoint_volumes",
                        "n_timepoints": 10,
                        "bytes": 1024,
                        "study_json_sha256": "0" * 64,
                    }
                ],
            }
        ],
    }


# --- the templates themselves must be valid ----------------------------------


def test_patient_template_is_valid(patient):
    assert validate_patient(patient) == []


def test_study_template_is_valid(study):
    assert validate_study(study) == []


def test_manifest_fixture_is_valid(manifest):
    assert validate_manifest(manifest) == []


# --- patient.json ------------------------------------------------------------


def test_age_over_89_must_be_suppressed(patient):
    patient["demographics"]["age_years"] = 94
    patient["demographics"]["age_90_or_older"] = True
    assert "E_AGE_OVER_89_NOT_SUPPRESSED" in _codes(validate_patient(patient))


def test_age_90_flag_with_null_age_is_fine(patient):
    patient["demographics"]["age_years"] = None
    patient["demographics"]["age_90_or_older"] = True
    assert validate_patient(patient) == []


def test_age_above_89_is_rejected_by_the_schema(patient):
    """The schema bound catches the case where the flag was simply not set."""
    patient["demographics"]["age_years"] = 94
    assert "E_SCHEMA" in _codes(validate_patient(patient))


def test_no_age_at_all_is_flagged(patient):
    patient["demographics"]["age_years"] = None
    patient["demographics"]["age_90_or_older"] = False
    assert "E_AGE_MISSING" in _codes(validate_patient(patient))


def test_bad_sex_enum(patient):
    patient["demographics"]["sex"] = "female"
    assert "E_SCHEMA" in _codes(validate_patient(patient))


def test_bad_reason_for_scan_enum(patient):
    patient["reason_for_scan"]["category"] = "because"
    assert "E_SCHEMA" in _codes(validate_patient(patient))


def test_bad_patient_id_pattern(patient):
    patient["patient_id"] = "STAN-0001A"
    assert "E_SCHEMA" in _codes(validate_patient(patient))


def test_unknown_field_is_rejected(patient):
    """additionalProperties is false so typos surface instead of being ignored."""
    patient["demographics"]["agee_years"] = 62
    assert "E_SCHEMA" in _codes(validate_patient(patient))


def test_missing_deidentification_block(patient):
    del patient["deidentification"]
    assert "E_SCHEMA" in _codes(validate_patient(patient))


def test_leaked_date_in_free_text(patient):
    patient["disease_state"]["notes"] = "Repaired on 1962-04-17 at the referring hospital."
    findings = validate_patient(patient)
    assert "E_DATE_IN_METADATA" in _codes(findings)
    assert any("disease_state.notes" == f.path for f in findings)


def test_leaked_date_inside_a_list(patient):
    patient["treatment_history"][0]["notes"] = "Follow-up 2019/03/02"
    assert "E_DATE_IN_METADATA" in _codes(validate_patient(patient))


def test_date_under_deidentification_is_allowed(patient):
    """A date-shift description legitimately mentions dates."""
    patient["deidentification"]["tooling"] = "CTP, date shift anchored at 2000-01-01"
    assert validate_patient(patient) == []


def test_relative_timing_is_not_mistaken_for_a_date(patient):
    patient["treatment_history"][0]["days_relative_to_scan"] = -420
    assert validate_patient(patient) == []


# --- study.json --------------------------------------------------------------


def test_timepoint_count_mismatch(study):
    study["n_timepoints"] = 11
    assert "E_TIMEPOINT_COUNT_MISMATCH" in _codes(validate_study(study))


def test_timepoint_indices_must_be_contiguous(study):
    study["timepoints"][3]["index"] = 99
    assert "E_TIMEPOINT_INDEX_NOT_CONTIGUOUS" in _codes(validate_study(study))


def test_single_timepoint_is_rejected(study):
    """OpenH-4D is a 4D initiative; one time point is a 3D study."""
    study["timepoints"] = study["timepoints"][:1]
    study["n_timepoints"] = 1
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_bad_modality(study):
    study["modality"] = "XA"
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_bad_organ(study):
    study["organ"] = "brain"
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_bad_file_layout(study):
    study["file_layout"] = "whatever"
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_geometry_is_required(study):
    del study["geometry"]
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_negative_slice_spacing(study):
    study["geometry"]["slice_spacing_mm"] = -1.0
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_in_plane_must_be_a_pair(study):
    study["geometry"]["in_plane_mm"] = [0.78, 0.78, 0.78]
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_waiver_needs_a_justification(study):
    study["waivers"] = [{"rule": "ct_in_plane_resolution"}]
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_empty_waiver_justification_is_rejected(study):
    study["waivers"] = [{"rule": "ct_in_plane_resolution", "justification": ""}]
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_phase_fraction_out_of_range(study):
    study["timepoints"][0]["phase_fraction"] = 1.5
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_study_id_must_carry_a_letter_suffix(study):
    study["study_id"] = "STAN-0001"
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_us_source_enum(study):
    study["modality"] = "US"
    study["us"]["source"] = "handheld"
    assert "E_SCHEMA" in _codes(validate_study(study))


def test_study_template_deep_copy_is_independent(study):
    """Guard against a fixture that leaks mutations between tests."""
    before = copy.deepcopy(study)
    study["notes"] = "mutated"
    assert before["notes"] != study["notes"]


# --- manifest.json -----------------------------------------------------------


def test_manifest_requires_counts(manifest):
    del manifest["counts"]
    assert "E_SCHEMA" in _codes(validate_manifest(manifest))


def test_manifest_sha_must_be_hex64(manifest):
    manifest["patients"][0]["patient_json_sha256"] = "nothex"
    assert "E_SCHEMA" in _codes(validate_manifest(manifest))


def test_manifest_study_id_pattern(manifest):
    manifest["patients"][0]["studies"][0]["study_id"] = "stan-0001a"
    assert "E_SCHEMA" in _codes(validate_manifest(manifest))
