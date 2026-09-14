# SPDX-License-Identifier: Apache-2.0
"""Vendor 4D ultrasound detection, gating and fallback.

The vendor decoder bodies are not finished -- neither OpenH-4D example dataset
contains vendor 4D echo, so there are no real bytes to build against. What is
finished, and what these tests cover, is everything around them: recognising the
vendor, refusing output a decoder cannot vouch for, and turning both cases into
an instruction the contributor can act on.

That structure is the point. The failure this module exists to prevent is a
proprietary 4D echo file converted by a general-purpose tool into a volume that
opens cleanly with the wrong geometry.
"""

import numpy as np
import pytest

from openh4d import us_vendor
from openh4d.synthetic_dicom import write_series
from openh4d.us_vendor import (
    VENDOR_GE_KRETZ,
    VENDOR_PHILIPS,
    DecodedSeries,
    DecoderNotImplemented,
    UltrasoundVendorError,
    decode,
    detect,
    self_check,
)


def write_us(path, private_creator=None, modality="US"):
    """One DICOM instance with an optional vendor private block."""
    import pydicom

    source = write_series(path.parent / "src", n_timepoints=2, shape=(4, 4, 2))
    target = sorted(source.glob("*.dcm"))[0]
    dataset = pydicom.dcmread(str(target))
    dataset.Modality = modality
    if private_creator:
        block = dataset.private_block(0x200D, private_creator, create=True)
        block.add_new(0x01, "OB", b"\x00\x01")
    dataset.save_as(str(path), enforce_file_format=True)
    return path


def good_series(n_timepoints=3):
    volumes = np.stack(
        [np.arange(4 * 4 * 2, dtype=np.int16).reshape(4, 4, 2) + t for t in range(n_timepoints)]
    )
    return DecodedSeries(volumes=volumes, voxel_mm=(0.5, 0.5, 0.5))


# --- detection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "creator,vendor",
    [
        ("Philips US Imaging DD 045", VENDOR_PHILIPS),
        ("PHILIPS IMAGING DD 001", VENDOR_PHILIPS),
        ("Philips3D", VENDOR_PHILIPS),
        ("KRETZ_US", VENDOR_GE_KRETZ),
        ("GE Kretz US", VENDOR_GE_KRETZ),
    ],
)
def test_vendor_blocks_are_detected(tmp_path, creator, vendor):
    path = write_us(tmp_path / "vendor.dcm", private_creator=creator)
    assert detect(path) == vendor


def test_plain_ultrasound_is_not_claimed(tmp_path):
    """No private block means the ordinary DICOM path handles it."""
    assert detect(write_us(tmp_path / "plain.dcm")) is None


def test_a_ct_with_a_philips_block_is_not_claimed(tmp_path):
    """The vendor path is for 4D echo, not for anything Philips ever wrote."""
    path = write_us(tmp_path / "ct.dcm", private_creator="Philips US Imaging DD 045", modality="CT")
    assert detect(path) is None


def test_unknown_private_creator_is_not_claimed(tmp_path):
    assert detect(write_us(tmp_path / "other.dcm", private_creator="ACME WIDGETS")) is None


def test_a_non_dicom_file_is_not_claimed(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("nothing", encoding="utf-8")
    assert detect(path) is None


# --- the self-check gate -----------------------------------------------------


def test_a_good_series_passes():
    assert self_check(good_series()) == []


def test_a_single_timepoint_is_rejected():
    problems = self_check(good_series(n_timepoints=1))
    assert any("at least 2" in p for p in problems)


def test_an_implausible_voxel_size_is_rejected():
    """Metres read as millimetres is the classic way a decoder goes wrong."""
    series = good_series()
    series.voxel_mm = (0.0005, 0.0005, 0.0005)
    assert any("plausible range" in p for p in self_check(series))


def test_an_absurdly_large_voxel_is_rejected():
    series = good_series()
    series.voxel_mm = (50.0, 50.0, 50.0)
    assert any("plausible range" in p for p in self_check(series))


def test_a_nonpositive_voxel_size_is_rejected():
    series = good_series()
    series.voxel_mm = (0.5, 0.0, 0.5)
    assert any("not positive" in p for p in self_check(series))


def test_a_constant_volume_is_rejected():
    """All-same voxels means nothing was decoded, however well-formed the array is."""
    series = good_series()
    series.volumes = np.zeros_like(series.volumes)
    assert any("same value" in p for p in self_check(series))


def test_non_finite_values_are_rejected():
    series = good_series()
    series.volumes = series.volumes.astype(np.float32)
    series.volumes[0, 0, 0, 0] = np.nan
    assert any("NaN" in p for p in self_check(series))


def test_a_wrong_rank_is_rejected():
    series = good_series()
    series.volumes = series.volumes[0]
    assert any("expected a 4D array" in p for p in self_check(series))


def test_affine_is_built_from_the_voxel_size():
    series = good_series()
    series.voxel_mm = (0.4, 0.5, 0.6)
    series.origin_mm = (1.0, 2.0, 3.0)
    affine = series.affine()
    assert list(np.diag(affine)[:3]) == [0.4, 0.5, 0.6]
    assert list(affine[:3, 3]) == [1.0, 2.0, 3.0]


# --- decoders and fallback ---------------------------------------------------


@pytest.mark.parametrize("vendor", [VENDOR_PHILIPS, VENDOR_GE_KRETZ])
def test_unfinished_decoders_say_so_rather_than_guessing(vendor):
    """A decoder written against a guessed layout is the failure being avoided."""
    with pytest.raises(DecoderNotImplemented, match="not finished"):
        us_vendor.DECODERS[vendor](None)


def test_an_unrecognised_file_gets_the_export_instruction(tmp_path):
    path = write_us(tmp_path / "plain.dcm")
    with pytest.raises(UltrasoundVendorError) as excinfo:
        decode(path)
    message = str(excinfo.value)
    assert "convert_nrrd" in message
    assert "NRRD" in message


def test_a_declining_decoder_gets_the_export_instruction(tmp_path, monkeypatch):
    """Self-check failure must route to the fallback, not emit the bad volume."""

    def bad_decoder(_path):
        series = good_series()
        series.voxel_mm = (0.0001, 0.0001, 0.0001)
        return series

    monkeypatch.setitem(us_vendor.DECODERS, VENDOR_PHILIPS, bad_decoder)
    path = write_us(tmp_path / "philips.dcm", private_creator="Philips US Imaging DD 045")

    with pytest.raises(UltrasoundVendorError) as excinfo:
        decode(path)
    message = str(excinfo.value)
    assert "cannot vouch for" in message
    assert "convert_nrrd" in message


def test_a_working_decoder_is_stamped_with_its_version(tmp_path, monkeypatch):
    monkeypatch.setitem(us_vendor.DECODERS, VENDOR_PHILIPS, lambda _p: good_series())
    path = write_us(tmp_path / "philips.dcm", private_creator="Philips US Imaging DD 045")
    decoded = decode(path)
    assert decoded.decoder.startswith("philips ")


def test_convert_study_writes_volumes_and_records_the_decoder(tmp_path, monkeypatch):
    import json

    monkeypatch.setitem(us_vendor.DECODERS, VENDOR_PHILIPS, lambda _p: good_series())

    study_dir = tmp_path / "STAN-0001A"
    (study_dir / "dicom").mkdir(parents=True)
    write_us(study_dir / "dicom" / "00000.dcm", private_creator="Philips US Imaging DD 045")
    (study_dir / "study.json").write_text(
        json.dumps({"schema": "openh-4d/study/1.0", "study_id": "STAN-0001A"}), encoding="utf-8"
    )

    report = us_vendor.convert_study(study_dir, VENDOR_PHILIPS)
    assert report["n_timepoints"] == 3
    assert [f.name for f in sorted(study_dir.glob("t*.nii.gz"))] == report["files"]

    study = json.loads((study_dir / "study.json").read_text("utf-8"))
    assert study["modality"] == "US"
    assert study["us"]["vendor_decoder"].startswith("philips ")
    assert study["geometry"]["slice_spacing_mm"] == 0.5


def test_convert_study_with_no_dicom_is_a_clear_error(tmp_path):
    study_dir = tmp_path / "STAN-0001A"
    (study_dir / "dicom").mkdir(parents=True)
    with pytest.raises(UltrasoundVendorError, match="no DICOM files"):
        us_vendor.convert_study(study_dir, VENDOR_PHILIPS)


# --- integration with the DICOM path -----------------------------------------


def test_convert_dicom_routes_vendor_ultrasound_away_from_dcm2niix(tmp_path):
    """dcm2niix on a private-tag 4D echo produces a file that is quietly wrong."""
    from openh4d.convert_dicom import detect_vendor_ultrasound

    study_dir = tmp_path / "STAN-0001A"
    (study_dir / "dicom" / "t0000").mkdir(parents=True)
    write_us(
        study_dir / "dicom" / "t0000" / "00000.dcm",
        private_creator="Philips US Imaging DD 045",
    )
    assert detect_vendor_ultrasound(study_dir) == VENDOR_PHILIPS


def test_ordinary_dicom_is_left_to_dcm2niix(tmp_path):
    from openh4d.convert_dicom import detect_vendor_ultrasound

    study_dir = tmp_path / "STAN-0001A"
    phase = study_dir / "dicom" / "t0000"
    phase.mkdir(parents=True)
    source = write_series(tmp_path / "src", n_timepoints=2, shape=(4, 4, 2))
    for index, path in enumerate(sorted(source.glob("*.dcm"))[:2]):
        (phase / f"{index:05d}.dcm").write_bytes(path.read_bytes())

    assert detect_vendor_ultrasound(study_dir) is None
