# SPDX-License-Identifier: Apache-2.0
"""Converting non-DICOM 4D sources.

The fixture is a byte-level replica of the header in the real
``TruncalValve_4DCT.seq.nrrd``, read over the wire before this converter was
written. Two properties of that file drive everything here and are silent
failures if handled wrongly: the time axis comes first, and the declared space
is RAS while SimpleITK normalizes to LPS.

The orientation test is the load-bearing one. A wrong handedness flip mirrors
the anatomy without raising anything -- the volume opens, the spacing is right,
and left and right are swapped.
"""

import gzip
import json

import nibabel as nib
import numpy as np
import pytest

from openh4d.convert_nrrd import (
    NrrdConvertError,
    affine_from_image,
    convert,
    read_source,
    split_timepoints,
)

# Geometry copied verbatim from the real TruncalValve_4DCT.seq.nrrd header.
REAL_SPACE_DIRECTIONS = (0.599609375, 0.599609375, 0.69999999999999984)
REAL_SPACE_ORIGIN = (-145.19999694824219, 8.9416399002075195, 903.09997558593739)


def write_sequence_nrrd(
    path,
    *,
    n_timepoints=3,
    shape=(6, 5, 4),
    spacing=REAL_SPACE_DIRECTIONS,
    origin=REAL_SPACE_ORIGIN,
    space="right-anterior-superior",
    include_index_values=True,
):
    """Write a 3D Slicer sequence NRRD with the real file's header structure."""
    nx, ny, nz = shape
    data = np.arange(n_timepoints * nx * ny * nz, dtype="<i2").reshape(nz, ny, nx, n_timepoints)

    lines = [
        "NRRD0005",
        "type: short",
        "dimension: 4",
        f"space: {space}",
        f"sizes: {n_timepoints} {nx} {ny} {nz}",
        f"space directions: none ({spacing[0]},0,0) (0,{spacing[1]},0) (0,0,{spacing[2]})",
        "kinds: list domain domain domain",
        'labels: "frame" "" "" ""',
        "endian: little",
        "encoding: gzip",
        f"space origin: ({origin[0]},{origin[1]},{origin[2]})",
        "measurement frame: (1,0,0) (0,1,0) (0,0,1)",
        "DataNodeClassName:=vtkMRMLScalarVolumeNode",
    ]
    if include_index_values:
        lines.append("axis 0 index type:=numeric")
        values = " ".join(str(i) for i in range(n_timepoints))
        lines.append(f"axis 0 index values:={values}")

    header = ("\n".join(lines) + "\n\n").encode()
    path.write_bytes(header + gzip.compress(data.tobytes()))
    return path, data


def write_3d_nrrd(path, *, shape=(6, 5, 4), fill=7):
    nx, ny, nz = shape
    data = np.full((nz, ny, nx), fill, dtype="<i2")
    header = (
        "NRRD0005\ntype: short\ndimension: 3\nspace: right-anterior-superior\n"
        f"sizes: {nx} {ny} {nz}\n"
        "space directions: (0.6,0,0) (0,0.6,0) (0,0,0.7)\n"
        "kinds: domain domain domain\nendian: little\nencoding: gzip\n"
        "space origin: (0,0,0)\n\n"
    ).encode()
    path.write_bytes(header + gzip.compress(data.tobytes()))
    return path


@pytest.fixture
def sequence(tmp_path):
    path, data = write_sequence_nrrd(tmp_path / "TruncalValve_replica.seq.nrrd")
    return path, data


# --- geometry ----------------------------------------------------------------


def test_affine_matches_the_declared_ras_geometry(sequence):
    """The file says RAS; SimpleITK hands back LPS; NIfTI wants RAS again."""
    path, _ = sequence
    affine = affine_from_image(read_source(path))
    expected = np.array(
        [
            [REAL_SPACE_DIRECTIONS[0], 0, 0, REAL_SPACE_ORIGIN[0]],
            [0, REAL_SPACE_DIRECTIONS[1], 0, REAL_SPACE_ORIGIN[1]],
            [0, 0, REAL_SPACE_DIRECTIONS[2], REAL_SPACE_ORIGIN[2]],
            [0, 0, 0, 1],
        ]
    )
    assert np.allclose(affine, expected, atol=1e-6)


def test_written_volumes_carry_the_ras_affine(sequence, tmp_path):
    path, _ = sequence
    convert(path, tmp_path / "STAN-0001A")
    affine = nib.load(str(tmp_path / "STAN-0001A" / "t0000.nii.gz")).affine
    assert "".join(nib.aff2axcodes(affine)) == "RAS"
    assert affine[0, 3] == pytest.approx(REAL_SPACE_ORIGIN[0])
    assert affine[1, 3] == pytest.approx(REAL_SPACE_ORIGIN[1])


def test_an_lps_source_maps_to_the_same_physical_point(tmp_path):
    """Both handednesses must place voxel (0,0,0) where the source said it was.

    A NIfTI affine always maps into RAS *space*, but the voxel axes themselves
    keep the source's direction -- so an LPS-declared file correctly comes out
    with LPS axis codes and sign-flipped x and y. What must not change is where
    a given voxel actually lands in the patient.
    """
    path, _ = write_sequence_nrrd(tmp_path / "lps.seq.nrrd", space="left-posterior-superior")
    convert(path, tmp_path / "STAN-0001A")
    affine = nib.load(str(tmp_path / "STAN-0001A" / "t0000.nii.gz")).affine

    assert "".join(nib.aff2axcodes(affine)) == "LPS"
    # The source declared its origin in LPS; in RAS that is x and y negated.
    assert affine[0, 3] == pytest.approx(-REAL_SPACE_ORIGIN[0])
    assert affine[1, 3] == pytest.approx(-REAL_SPACE_ORIGIN[1])
    assert affine[2, 3] == pytest.approx(REAL_SPACE_ORIGIN[2])


def test_voxel_axis_codes_are_recorded_in_study_json(tmp_path):
    """orientation records which way the voxel axes run, so a reader can tell."""
    ras, _ = write_sequence_nrrd(tmp_path / "ras.seq.nrrd")
    lps, _ = write_sequence_nrrd(tmp_path / "lps.seq.nrrd", space="left-posterior-superior")
    assert convert(ras, tmp_path / "A")["geometry"]["orientation"] == "RAS"
    assert convert(lps, tmp_path / "B")["geometry"]["orientation"] == "LPS"


def test_spacing_survives(sequence, tmp_path):
    report = convert(sequence[0], tmp_path / "STAN-0001A")
    assert report["geometry"]["in_plane_mm"] == [
        pytest.approx(REAL_SPACE_DIRECTIONS[0], abs=1e-6),
        pytest.approx(REAL_SPACE_DIRECTIONS[1], abs=1e-6),
    ]
    assert report["geometry"]["slice_spacing_mm"] == pytest.approx(0.7, abs=1e-6)


# --- the time axis -----------------------------------------------------------


def test_leading_list_axis_is_read_as_time_not_components(sequence):
    """SimpleITK reports a 3D image with N components; the N are time points."""
    path, _ = sequence
    image = read_source(path)
    assert image.GetDimension() == 3
    assert image.GetNumberOfComponentsPerPixel() == 3

    volumes, n_timepoints = split_timepoints(image)
    assert n_timepoints == 3
    assert volumes.shape == (3, 6, 5, 4)


def test_voxels_land_in_the_right_time_point(sequence, tmp_path):
    """A transposed time axis produces volumes that look fine and are scrambled."""
    path, data = sequence
    convert(path, tmp_path / "STAN-0001A")
    for index in range(3):
        volume = np.asanyarray(
            nib.load(str(tmp_path / "STAN-0001A" / f"t{index:04d}.nii.gz")).dataobj
        )
        assert volume.shape == (6, 5, 4)
        # data is indexed (z, y, x, t); the written volume is (x, y, z).
        assert volume[3, 2, 1] == data[1, 2, 3, index]


def test_writes_one_file_per_time_point(sequence, tmp_path):
    report = convert(sequence[0], tmp_path / "STAN-0001A")
    assert report["n_timepoints"] == 3
    assert report["files"] == ["t0000.nii.gz", "t0001.nii.gz", "t0002.nii.gz"]


# --- Slicer sequence metadata ------------------------------------------------


def test_sequence_index_values_become_phase_labels(sequence, tmp_path):
    """The index values are the only record of what the source called each frame."""
    report = convert(sequence[0], tmp_path / "STAN-0001A")
    assert report["phase_labels"] == ["0", "1", "2"]

    study = json.loads((tmp_path / "STAN-0001A" / "study.json").read_text("utf-8"))
    assert [tp["phase_label"] for tp in study["timepoints"]] == ["0", "1", "2"]


def test_missing_index_values_is_not_fatal(tmp_path):
    path, _ = write_sequence_nrrd(tmp_path / "bare.seq.nrrd", include_index_values=False)
    report = convert(path, tmp_path / "STAN-0001A")
    assert report["n_timepoints"] == 3
    assert report["phase_labels"] is None


def test_mismatched_index_values_are_discarded_not_trusted(tmp_path):
    path, _ = write_sequence_nrrd(tmp_path / "s.seq.nrrd", n_timepoints=3)
    text = path.read_bytes()
    broken = text.replace(b"axis 0 index values:=0 1 2", b"axis 0 index values:=0 1")
    path.write_bytes(broken)
    assert convert(path, tmp_path / "STAN-0001A")["phase_labels"] is None


# --- study.json --------------------------------------------------------------


def test_study_json_is_written(sequence, tmp_path):
    convert(sequence[0], tmp_path / "STAN-0001A")
    study = json.loads((tmp_path / "STAN-0001A" / "study.json").read_text("utf-8"))
    assert study["study_id"] == "STAN-0001A"
    assert study["patient_id"] == "STAN-0001"
    assert study["file_layout"] == "timepoint_volumes"
    assert study["n_timepoints"] == 3
    assert "SimpleITK" in study["source"]["converter"]


def test_contributor_fields_are_left_null(sequence, tmp_path):
    """Modality and organ are not in an NRRD header and must not be invented."""
    convert(sequence[0], tmp_path / "STAN-0001A")
    study = json.loads((tmp_path / "STAN-0001A" / "study.json").read_text("utf-8"))
    assert study["modality"] is None
    assert study["organ"] is None
    assert study["motion"] is None


def test_existing_study_json_is_updated_not_replaced(sequence, tmp_path):
    study_dir = tmp_path / "STAN-0001A"
    study_dir.mkdir()
    (study_dir / "study.json").write_text(
        json.dumps({"schema": "openh-4d/study/1.0", "modality": "CT", "organ": "heart"}),
        encoding="utf-8",
    )
    convert(sequence[0], study_dir)
    study = json.loads((study_dir / "study.json").read_text("utf-8"))
    assert study["modality"] == "CT"
    assert study["organ"] == "heart"
    assert study["n_timepoints"] == 3


def test_explicit_identifiers_override(sequence, tmp_path):
    convert(sequence[0], tmp_path / "out", study_id="DUKE-0007B", patient_id="DUKE-0007")
    study = json.loads((tmp_path / "out" / "study.json").read_text("utf-8"))
    assert study["study_id"] == "DUKE-0007B"
    assert study["patient_id"] == "DUKE-0007"


# --- directory of per-time-point volumes -------------------------------------


def test_directory_of_3d_volumes(tmp_path):
    source = tmp_path / "frames"
    source.mkdir()
    for index in range(4):
        write_3d_nrrd(source / f"frame{index:02d}.nrrd", fill=index + 1)

    report = convert(source, tmp_path / "STAN-0001A")
    assert report["n_timepoints"] == 4
    assert report["phase_labels"] == ["frame00", "frame01", "frame02", "frame03"]
    for index in range(4):
        volume = np.asanyarray(
            nib.load(str(tmp_path / "STAN-0001A" / f"t{index:04d}.nii.gz")).dataobj
        )
        assert volume.flat[0] == index + 1


def test_directory_with_mismatched_grids_is_refused(tmp_path):
    source = tmp_path / "frames"
    source.mkdir()
    write_3d_nrrd(source / "a.nrrd", shape=(6, 5, 4))
    write_3d_nrrd(source / "b.nrrd", shape=(6, 5, 5))
    with pytest.raises(NrrdConvertError, match="one voxel grid"):
        convert(source, tmp_path / "STAN-0001A")


def test_empty_directory_is_a_clear_error(tmp_path):
    source = tmp_path / "frames"
    source.mkdir()
    with pytest.raises(NrrdConvertError, match="no readable volumes"):
        convert(source, tmp_path / "STAN-0001A")


# --- failure modes -----------------------------------------------------------


def test_a_3d_file_is_refused_with_a_useful_message(tmp_path):
    """One volume is a 3D study; OpenH-4D collects 4D."""
    path = write_3d_nrrd(tmp_path / "single.nrrd")
    with pytest.raises(NrrdConvertError, match="at least two time points"):
        convert(path, tmp_path / "STAN-0001A")


def test_missing_source(tmp_path):
    with pytest.raises(NrrdConvertError, match="no such path"):
        convert(tmp_path / "nope.nrrd", tmp_path / "out")


def test_unreadable_source(tmp_path):
    path = tmp_path / "broken.nrrd"
    path.write_bytes(b"this is not a nrrd")
    with pytest.raises(NrrdConvertError, match="could not read"):
        convert(path, tmp_path / "out")


# --- verification ------------------------------------------------------------


def test_converted_study_verifies_once_the_contributor_fills_it_in(sequence, tmp_path):
    from openh4d.manifest import build_manifest, write_manifest
    from openh4d.synthetic import _patient_json, _write_json
    from openh4d.verify_layout import verify

    root = tmp_path / "submission"
    convert(sequence[0], root / "STAN-0001A")

    study_path = root / "STAN-0001A" / "study.json"
    study = json.loads(study_path.read_text("utf-8"))
    study.update({"modality": "CT", "organ": "heart", "motion": "cardiac", "coverage": "full"})
    study["ct"] = {"low_dose": False, "kvp": 120, "exposure_mas": 150, "recon_kernel": "test"}
    study_path.write_text(json.dumps(study, indent=2), encoding="utf-8")

    ehr = root / "STAN-0001_ehr"
    ehr.mkdir()
    _write_json(ehr / "patient.json", _patient_json("STAN-0001", ["STAN-0001A"], "heart"))
    (root / "README.md").write_text("---\nlicense: cc-by-4.0\n---\n# test\n", encoding="utf-8")
    (root / "LICENSE").write_text("CC BY 4.0\n", encoding="utf-8")
    write_manifest(root, build_manifest(root))

    report = verify(root)
    assert report.compliant, report.errors
