# SPDX-License-Identifier: Apache-2.0
"""The DICOM to NIfTI round trip, end to end through dcm2niix.

This is the highest-value test in the suite: it exercises the real pinned
binary, on all three operating systems, against a fixture whose correct content
is known. Everything else in the repo is metadata about volumes; this is the
step that produces them.

Skips cleanly when dcm2niix cannot be resolved offline, so a developer with no
network still gets a green suite.
"""

import json

import numpy as np
import pytest

from openh4d.convert_dicom import ConvertError, convert_study
from openh4d.dicom_index import build_plan
from openh4d.organize_dicom import organize
from openh4d.synthetic_dicom import write_series
from openh4d.verify_layout import verify

SMALL = (8, 8, 4)


@pytest.fixture
def organized(tmp_path):
    """A study directory in the shape convert_dicom expects."""
    source = write_series(tmp_path / "src", n_timepoints=4, shape=SMALL)
    out = tmp_path / "out"
    organize(
        build_plan(source),
        out,
        prefix="STAN",
        crosswalk=tmp_path / "outside" / "crosswalk.csv",
        apply=True,
    )
    return out / "STAN-0001A"


def _complete_sidecars(root):
    """Fill in the fields only a contributor can supply, so verification can pass."""
    study_path = root / "STAN-0001A" / "study.json"
    study = json.loads(study_path.read_text("utf-8"))
    study.update(
        {
            "organ": "heart",
            "motion": "cardiac",
            "coverage": "full",
            "protocol": "synthetic",
            "clinical_indication": "test",
        }
    )
    study["contrast"] = {"used": False, "agent": None, "phase": None}
    study["ct"] = {"low_dose": False, "kvp": 120, "exposure_mas": 150, "recon_kernel": "test"}
    study_path.write_text(json.dumps(study, indent=2), encoding="utf-8")

    patient_path = root / "STAN-0001_ehr" / "patient.json"
    patient = json.loads(patient_path.read_text("utf-8"))
    patient["demographics"]["age_years"] = 55
    patient["reason_for_scan"] = {"category": "research_only", "description": "test"}
    patient["deidentification"].update(
        {"performed_by": "test", "tooling": "synthetic", "attestation": "synthetic data"}
    )
    patient_path.write_text(json.dumps(patient, indent=2), encoding="utf-8")


# --- the round trip ----------------------------------------------------------


def test_converts_every_phase_directory(organized, dcm2niix_path):
    report = convert_study(organized, dcm2niix=dcm2niix_path)
    assert report["n_timepoints"] == 4
    assert report["files"] == ["t0000.nii.gz", "t0001.nii.gz", "t0002.nii.gz", "t0003.nii.gz"]
    for name in report["files"]:
        assert (organized / name).is_file()


def test_geometry_survives_the_round_trip(organized, dcm2niix_path):
    """A silent orientation or spacing change here would poison the whole corpus."""
    report = convert_study(organized, dcm2niix=dcm2niix_path)
    geometry = report["geometry"]
    assert geometry["in_plane_mm"] == [0.8, 0.8]
    assert geometry["slice_spacing_mm"] == pytest.approx(1.0)
    assert geometry["matrix"] == [8, 8, 4]
    assert geometry["orientation"] and len(geometry["orientation"]) == 3


def test_pixel_content_survives_the_round_trip(organized, dcm2niix_path):
    """The volumes must carry the moving sphere, not an empty grid."""
    import nibabel as nib

    convert_study(organized, dcm2niix=dcm2niix_path)
    first = np.asanyarray(nib.load(str(organized / "t0000.nii.gz")).dataobj)
    mid = np.asanyarray(nib.load(str(organized / "t0002.nii.gz")).dataobj)

    assert first.shape == (8, 8, 4)
    assert first.min() != first.max(), "the volume is constant, so nothing was converted"
    assert not np.array_equal(first, mid), "time points are identical, so there is no motion"


def test_study_json_is_updated_with_the_conversion(organized, dcm2niix_path):
    convert_study(organized, dcm2niix=dcm2niix_path)
    study = json.loads((organized / "study.json").read_text("utf-8"))

    assert study["n_timepoints"] == 4
    assert [tp["file"] for tp in study["timepoints"]] == [
        "t0000.nii.gz",
        "t0001.nii.gz",
        "t0002.nii.gz",
        "t0003.nii.gz",
    ]
    assert study["source"]["converter"].startswith("dcm2niix v1.0.")
    assert study["source"]["original_format"] == "DICOM"


def test_phase_labels_recovered_by_the_indexer_are_preserved(organized, dcm2niix_path):
    """The contributor's own phase labels are the only record of what the source said."""
    before = json.loads((organized / "study.json").read_text("utf-8"))
    labels_before = [tp["phase_label"] for tp in before["timepoints"]]

    convert_study(organized, dcm2niix=dcm2niix_path)
    after = json.loads((organized / "study.json").read_text("utf-8"))
    assert [tp["phase_label"] for tp in after["timepoints"]] == labels_before
    assert labels_before == ["0.0%", "25.0%", "50.0%", "75.0%"]


def test_dcm2niix_sidecars_are_kept(organized, dcm2niix_path):
    """The BIDS sidecar carries acquisition detail worth keeping next to the volume."""
    convert_study(organized, dcm2niix=dcm2niix_path)
    assert (organized / "t0000.json").is_file()


# --- DICOM retention ---------------------------------------------------------


def test_dicom_is_retained_by_default(organized, dcm2niix_path):
    report = convert_study(organized, dcm2niix=dcm2niix_path)
    assert (organized / "dicom").is_dir()
    assert report["dicom_retained"] is True

    study = json.loads((organized / "study.json").read_text("utf-8"))
    assert study["file_layout"] == "dicom_phases"
    assert study["derived_from_dicom"] is True


def test_drop_dicom_removes_the_tree_and_switches_the_layout(organized, dcm2niix_path):
    convert_study(organized, dcm2niix=dcm2niix_path, keep_dicom=False)
    assert not (organized / "dicom").exists()
    study = json.loads((organized / "study.json").read_text("utf-8"))
    assert study["file_layout"] == "timepoint_volumes"
    assert study["derived_from_dicom"] is False


# --- the full chain ----------------------------------------------------------


def test_organize_convert_verify_passes(organized, dcm2niix_path):
    """The gate for this phase: a synthetic DICOM pile becomes a compliant submission."""
    root = organized.parent
    convert_study(organized, dcm2niix=dcm2niix_path)
    _complete_sidecars(root)

    # The pieces the contributor writes by hand, not the tooling.
    (root / "README.md").write_text("---\nlicense: cc-by-4.0\n---\n# test\n", encoding="utf-8")
    (root / "LICENSE").write_text("CC BY 4.0\n", encoding="utf-8")
    from openh4d.manifest import build_manifest, write_manifest

    write_manifest(root, build_manifest(root))

    report = verify(root)
    assert report.compliant, report.errors
    assert report.summary["timepoints"] == 4


# --- failure modes -----------------------------------------------------------


def test_missing_dicom_directory_is_a_clear_error(tmp_path):
    study_dir = tmp_path / "STAN-0001A"
    study_dir.mkdir()
    with pytest.raises(ConvertError, match="does not exist"):
        convert_study(study_dir)


def test_empty_dicom_directory_is_a_clear_error(tmp_path, dcm2niix_path):
    study_dir = tmp_path / "STAN-0001A"
    (study_dir / "dicom").mkdir(parents=True)
    with pytest.raises(ConvertError, match="no per-phase subdirectories"):
        convert_study(study_dir, dcm2niix=dcm2niix_path)


def test_unconvertible_input_is_a_clear_error(tmp_path, dcm2niix_path):
    study_dir = tmp_path / "STAN-0001A"
    phase = study_dir / "dicom" / "t0000"
    phase.mkdir(parents=True)
    (phase / "00000.dcm").write_bytes(b"not dicom at all")
    with pytest.raises(ConvertError):
        convert_study(study_dir, dcm2niix=dcm2niix_path)


def test_inconsistent_geometry_across_time_points_is_refused(organized, dcm2niix_path):
    """Time points on different grids mean voxel (i,j,k) is not the same anatomy."""
    import pydicom

    stray = sorted((organized / "dicom" / "t0002").glob("*.dcm"))
    for path in stray:
        dataset = pydicom.dcmread(str(path))
        dataset.PixelSpacing = [1.6, 1.6]
        dataset.save_as(str(path), enforce_file_format=True)

    with pytest.raises(ConvertError, match="do not share one voxel grid"):
        convert_study(organized, dcm2niix=dcm2niix_path)


def test_missing_study_json_is_a_clear_error(organized, dcm2niix_path):
    (organized / "study.json").unlink()
    with pytest.raises(ConvertError, match="run organize_dicom first"):
        convert_study(organized, dcm2niix=dcm2niix_path)


def test_dcm2niix_warnings_are_surfaced_not_buried(organized, dcm2niix_path):
    """Slice-spacing and gantry-tilt warnings change whether a study is usable."""
    report = convert_study(organized, dcm2niix=dcm2niix_path)
    assert isinstance(report["warnings"], list)
    study = json.loads((organized / "study.json").read_text("utf-8"))
    if report["warnings"]:
        assert "dcm2niix warnings" in study["notes"]
