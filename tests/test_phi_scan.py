# SPDX-License-Identifier: Apache-2.0
"""Detecting protected health information.

The tooling verifies de-identification; it does not perform it. These tests
check both halves of that: an identified source is refused, and a finished
submission is audited. Both directions matter -- a scanner that never fires is
worthless, and one that fires on everything gets ignored.
"""

import json

import pytest

from openh4d import phi_scan
from openh4d.phi_scan import (
    looks_like_a_pseudonym,
    scan_dicom_source,
    scan_submission,
    scan_text,
)
from openh4d.synthetic import generate
from openh4d.synthetic_dicom import write_series

SMALL = (8, 8, 4)


def codes(report):
    return {f.code for f in report.findings}


# --- free text ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,code",
    [
        ("Repaired on 1962-04-17.", "E_PHI_DATE"),
        ("Scanned 2019/03/02", "E_PHI_DATE"),
        ("SSN 123-45-6789", "E_PHI_SSN"),
        ("call 555-123-4567", "E_PHI_PHONE"),
        ("contact jane.doe@hospital.org", "E_PHI_EMAIL"),
        ("MRN: 00123456", "E_PHI_MRN"),
        ("patient is 94 years old", "E_PHI_AGE_OVER_89"),
        ("97yo female", "E_PHI_AGE_OVER_89"),
    ],
)
def test_identifier_shapes_are_detected(text, code):
    assert code in {f.code for f in scan_text(text)}


@pytest.mark.parametrize(
    "text",
    [
        "Tetralogy of Fallot, status post repair",
        "62 years old",
        "89yo male",
        "slice spacing 1.25 mm",
        "surgery 420 days before the scan",
        "phase 50.0%",
    ],
)
def test_ordinary_clinical_text_is_not_flagged(text):
    """A scanner that fires on normal metadata gets ignored, which is worse than none."""
    assert scan_text(text) == []


def test_evidence_is_redacted():
    """A PHI report must not itself become a second copy of the PHI."""
    finding = scan_text("SSN 123-45-6789")[0]
    assert "123-45-6789" not in finding.evidence
    assert finding.evidence.startswith("123")


@pytest.mark.parametrize(
    "value", ["ANON", "anonymized", "removed", "none", "STAN-0001", "12345", "", "^^^"]
)
def test_pseudonyms_are_recognised(value):
    assert looks_like_a_pseudonym(value)


@pytest.mark.parametrize("value", ["DOE^JANE", "Jane Doe", "Smith"])
def test_real_names_are_not_pseudonyms(value):
    assert not looks_like_a_pseudonym(value)


# --- DICOM source pre-flight -------------------------------------------------


def test_identified_source_is_refused(tmp_path):
    source = write_series(tmp_path / "phi", n_timepoints=2, shape=SMALL, include_phi=True)
    report = scan_dicom_source(source)
    assert not report.clean
    assert "E_PHI_DICOM_TAG" in codes(report)
    assert "E_PHI_DICOM_DATE" in codes(report)


def test_identified_source_names_each_offending_tag(tmp_path):
    source = write_series(tmp_path / "phi", n_timepoints=2, shape=SMALL, include_phi=True)
    evidence = " ".join(f.evidence for f in scan_dicom_source(source).findings)
    for keyword in ("PatientName", "PatientBirthDate", "InstitutionName", "AccessionNumber"):
        assert keyword in evidence


def test_a_pseudonymised_source_passes(tmp_path):
    """The default fixture uses ANON^ANON and no dates, which is what clean looks like."""
    source = write_series(tmp_path / "clean", n_timepoints=2, shape=SMALL)
    assert scan_dicom_source(source).clean


def test_findings_are_deduplicated(tmp_path):
    """A 100,000-file export must not produce 100,000 identical findings."""
    source = write_series(tmp_path / "phi", n_timepoints=4, shape=SMALL, include_phi=True)
    report = scan_dicom_source(source)
    assert report.n_files_scanned > len(report.findings)


def test_sampling_bounds_the_work(tmp_path, monkeypatch):
    source = write_series(tmp_path / "phi", n_timepoints=4, shape=SMALL, include_phi=True)
    sampled = scan_dicom_source(source, max_files=4)
    assert sampled.n_files_scanned <= 5
    assert not sampled.clean


def test_burned_in_annotation_is_caught(tmp_path):
    """Stripping tags does not help when the identifier is rendered into the pixels."""
    import pydicom

    source = write_series(tmp_path / "burn", n_timepoints=2, shape=SMALL)
    target = sorted(source.glob("*.dcm"))[0]
    dataset = pydicom.dcmread(str(target))
    dataset.BurnedInAnnotation = "YES"
    dataset.save_as(str(target), enforce_file_format=True)

    assert "E_PHI_BURNED_IN" in codes(scan_dicom_source(source))


def test_private_tags_are_a_warning_not_a_blocker(tmp_path):
    """Vendors hide arbitrary data there; worth surfacing, not worth blocking on."""
    import pydicom

    source = write_series(tmp_path / "private", n_timepoints=2, shape=SMALL)
    target = sorted(source.glob("*.dcm"))[0]
    dataset = pydicom.dcmread(str(target))
    block = dataset.private_block(0x000B, "OPENH4D TEST", create=True)
    block.add_new(0x01, "LO", "vendor payload")
    dataset.save_as(str(target), enforce_file_format=True)

    report = scan_dicom_source(source)
    assert "W_PHI_PRIVATE_TAGS" in codes(report)
    assert report.clean, "a private tag alone must not block a submission"


def test_non_dicom_files_do_not_break_the_scan(tmp_path):
    source = write_series(tmp_path / "s", n_timepoints=2, shape=SMALL)
    (source / "notes.txt").write_text("nothing here", encoding="utf-8")
    assert scan_dicom_source(source).clean


# --- submission audit --------------------------------------------------------


@pytest.fixture
def submission(tmp_path):
    return generate(tmp_path / "submission", n_timepoints=2, shape=SMALL)


def test_clean_submission_passes(submission):
    report = scan_submission(submission)
    assert report.clean, [f.as_dict() for f in report.findings]


def test_leaked_date_in_a_sidecar_is_caught(submission):
    path = submission / "OH4D-0001_ehr" / "patient.json"
    patient = json.loads(path.read_text("utf-8"))
    patient["disease_state"]["notes"] = "Repaired 1962-04-17"
    path.write_text(json.dumps(patient, indent=2), encoding="utf-8")

    report = scan_submission(submission)
    assert "E_PHI_DATE" in codes(report)
    assert any("disease_state.notes" in f.path for f in report.findings)


def test_date_under_deidentification_is_allowed(submission):
    path = submission / "OH4D-0001_ehr" / "patient.json"
    patient = json.loads(path.read_text("utf-8"))
    patient["deidentification"]["tooling"] = "CTP, dates shifted from 2000-01-01"
    path.write_text(json.dumps(patient, indent=2), encoding="utf-8")
    assert scan_submission(submission).clean


def test_free_text_report_is_scanned(submission):
    reports = submission / "OH4D-0001_ehr" / "reports"
    reports.mkdir()
    (reports / "note.txt").write_text("Jane Doe, seen 2019/03/02, MRN 00123456", encoding="utf-8")
    report = scan_submission(submission)
    assert not report.clean
    assert {"E_PHI_DATE", "E_PHI_MRN"} <= codes(report)


def test_the_data_card_is_scanned(submission):
    readme = submission / "README.md"
    readme.write_text(readme.read_text("utf-8") + "\nContact 555-123-4567\n", encoding="utf-8")
    assert "E_PHI_PHONE" in codes(scan_submission(submission))


def test_a_filename_carrying_a_date_is_caught(submission):
    """Filenames travel with the data just as surely as fields do."""
    (submission / "OH4D-0001A" / "scan_1962-04-17.txt").write_text("x", encoding="utf-8")
    assert "E_PHI_FILENAME" in codes(scan_submission(submission))


def test_retained_dicom_is_scanned(tmp_path, submission):
    import pydicom

    source = write_series(tmp_path / "src", n_timepoints=2, shape=SMALL, include_phi=True)
    target_dir = submission / "OH4D-0001A" / "dicom" / "t0000"
    target_dir.mkdir(parents=True)
    dataset = pydicom.dcmread(str(sorted(source.glob("*.dcm"))[0]))
    dataset.save_as(str(target_dir / "00000.dcm"), enforce_file_format=True)

    assert "E_PHI_DICOM_TAG" in codes(scan_submission(submission))


# --- CLI ---------------------------------------------------------------------


def test_cli_exit_codes(tmp_path, capsys):
    clean = write_series(tmp_path / "clean", n_timepoints=2, shape=SMALL)
    dirty = write_series(tmp_path / "dirty", n_timepoints=2, shape=SMALL, include_phi=True)

    assert phi_scan.main([str(clean)]) == 0
    assert phi_scan.main([str(dirty)]) == 1
    captured = capsys.readouterr()
    assert "will not de-identify it for you" in captured.err


def test_cli_missing_path(tmp_path, capsys):
    assert phi_scan.main([str(tmp_path / "nope")]) == 1
