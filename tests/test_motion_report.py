# SPDX-License-Identifier: Apache-2.0
"""Measuring motion in a 4D study.

The module emits numbers and leaves the imaging judgement to a reviewer, so
these tests check that the numbers are right and that the one unambiguous
defect -- duplicated time points -- is called out without hedging.
"""

import json

import nibabel as nib
import numpy as np
import pytest

from openh4d.motion_report import (
    build_report,
    centroid_displacement,
    centroid_track,
    find_duplicate_timepoints,
    frame_differences,
    intensity_curve,
    load_series,
    phase_label_order,
)
from openh4d.synthetic import generate, moving_sphere

SMALL = (12, 12, 6)


@pytest.fixture
def study(tmp_path):
    root = generate(tmp_path / "submission", n_timepoints=6, shape=SMALL)
    return root / "OH4D-0001A"


def _write_volumes(study_dir, volumes):
    study_dir.mkdir(parents=True, exist_ok=True)
    for index in range(volumes.shape[0]):
        nib.save(
            nib.Nifti1Image(volumes[index], np.eye(4)),
            str(study_dir / f"t{index:04d}.nii.gz"),
        )
    return study_dir


# --- loading -----------------------------------------------------------------


def test_load_series_shape(study):
    volumes, labels = load_series(study)
    assert volumes.shape == (6, *SMALL)
    assert labels == [f"t{i:04d}" for i in range(6)]


def test_load_series_handles_the_single_4d_form(tmp_path):
    root = generate(tmp_path / "f", n_timepoints=4, shape=(8, 8, 4), form="single_4d")
    volumes, _ = load_series(root / "OH4D-0001A")
    assert volumes.shape == (4, 8, 8, 4)


def test_load_series_rejects_a_3d_single_file(tmp_path):
    study_dir = tmp_path / "STAN-0001A"
    study_dir.mkdir()
    nib.save(
        nib.Nifti1Image(np.zeros((4, 4, 2), dtype=np.int16), np.eye(4)),
        str(study_dir / "image4d.nii.gz"),
    )
    with pytest.raises(ValueError, match="no time axis"):
        load_series(study_dir)


def test_load_series_with_no_volumes(tmp_path):
    empty = tmp_path / "STAN-0001A"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        load_series(empty)


# --- the measurements --------------------------------------------------------


def test_centroid_tracks_a_moving_sphere(study):
    """The synthetic fixture moves on a closed loop, so the centroid must move."""
    volumes, _ = load_series(study)
    track = centroid_track(volumes)
    displacement = centroid_displacement(track)

    assert displacement[0] == pytest.approx(0.0)
    assert max(displacement) > 1.0, "a moving sphere must shift the centre of mass"


def test_centroid_is_flat_for_a_static_volume(tmp_path):
    static = np.repeat(moving_sphere(SMALL, 1), 5, axis=0)
    study_dir = _write_volumes(tmp_path / "STAN-0001A", static)
    volumes, _ = load_series(study_dir)
    assert max(centroid_displacement(centroid_track(volumes))) == pytest.approx(0.0)


def test_intensity_curve_has_one_value_per_time_point(study):
    volumes, _ = load_series(study)
    assert len(intensity_curve(volumes)) == 6


def test_frame_differences_are_zero_only_when_frames_repeat(tmp_path):
    volumes = moving_sphere(SMALL, 4)
    volumes[2] = volumes[1]
    study_dir = _write_volumes(tmp_path / "STAN-0001A", volumes)
    loaded, _ = load_series(study_dir)
    differences = frame_differences(loaded)
    assert differences[1] == 0.0
    assert differences[0] > 0.0


def test_duplicate_detection_groups_identical_volumes():
    volumes = moving_sphere(SMALL, 5)
    volumes[3] = volumes[1]
    groups = find_duplicate_timepoints(volumes)
    assert groups == [[1, 3]]


def test_no_duplicates_in_a_genuine_sequence():
    assert find_duplicate_timepoints(moving_sphere(SMALL, 5)) == []


def test_duplicate_detection_handles_three_way_repeats():
    volumes = moving_sphere(SMALL, 5)
    volumes[2] = volumes[0]
    volumes[4] = volumes[0]
    assert find_duplicate_timepoints(volumes) == [[0, 2, 4]]


# --- phase ordering ----------------------------------------------------------


def test_monotonic_phase_fractions_pass():
    study = {"timepoints": [{"phase_fraction": f} for f in (0.0, 0.25, 0.5, 0.75)]}
    result = phase_label_order(study)
    assert result["checked"] and result["monotonic"]


def test_scrambled_phase_fractions_are_caught():
    """Out-of-order phases scramble the cycle without changing any volume."""
    study = {"timepoints": [{"phase_fraction": f} for f in (0.0, 0.5, 0.25, 0.75)]}
    result = phase_label_order(study)
    assert result["checked"] and not result["monotonic"]


def test_missing_phase_fractions_are_not_a_failure():
    study = {"timepoints": [{"phase_fraction": None}, {"phase_fraction": None}]}
    assert phase_label_order(study)["checked"] is False


# --- the report --------------------------------------------------------------


def test_report_on_a_genuine_4d_study(study):
    report = build_report(study)
    assert report["n_timepoints"] == 6
    assert report["has_motion"] is True
    assert report["duplicate_timepoints"] == []
    assert report["observations"] == []
    assert report["peak_centroid_travel_voxels"] > 1.0
    assert len(report["intensity_curve"]) == 6
    assert len(report["consecutive_frame_difference"]) == 5


def test_report_flags_a_frozen_study(tmp_path):
    static = np.repeat(moving_sphere(SMALL, 1), 4, axis=0)
    study_dir = _write_volumes(tmp_path / "STAN-0001A", static)
    report = build_report(study_dir)

    assert report["has_motion"] is False
    assert report["duplicate_timepoints"] == [[0, 1, 2, 3]]
    assert any("byte-for-byte identical" in o for o in report["observations"])
    assert any("indistinguishable from noise" in o for o in report["observations"])
    assert any("effectively static" in o for o in report["observations"])


def test_report_localizes_a_single_frozen_step(tmp_path):
    volumes = moving_sphere(SMALL, 4)
    volumes[2] = volumes[1]
    study_dir = _write_volumes(tmp_path / "STAN-0001A", volumes)
    report = build_report(study_dir)
    assert any("time points 1 and 2 are identical" in o for o in report["observations"])


def test_report_reads_study_json_context(study):
    report = build_report(study)
    assert report["study_id"] == "OH4D-0001A"
    assert report["modality"] == "CT"
    assert report["organ"] == "heart"
    assert report["motion"] == "cardiac"


def test_report_flags_scrambled_phase_labels(study):
    path = study / "study.json"
    document = json.loads(path.read_text("utf-8"))
    document["timepoints"][2]["phase_fraction"] = 0.9
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    report = build_report(study)
    assert any("not monotonic" in o for o in report["observations"])


def test_report_survives_a_missing_study_json(study):
    (study / "study.json").unlink()
    report = build_report(study)
    assert report["n_timepoints"] == 6
    assert report["modality"] is None


# --- closed cycles -----------------------------------------------------------
#
# Found against the real TruncalValve_4DCT.seq.nrrd: its 21 frames close the
# loop, with frame 20 a byte-for-byte copy of frame 0. That is a normal encoding
# for a gated acquisition, and flagging it as a collapsed phase sort would
# condemn a large share of real 4D data.


def test_closed_cycle_is_recognised(tmp_path):
    volumes = moving_sphere(SMALL, 8)
    volumes[7] = volumes[0]
    study_dir = _write_volumes(tmp_path / "STAN-0001A", volumes)
    report = build_report(study_dir)

    assert report["closed_cycle"] is True
    assert report["n_distinct_timepoints"] == 7
    assert report["has_motion"] is True
    assert any("closed cycle" in o for o in report["observations"])
    assert not any("phase sort collapsed" in o for o in report["observations"])


def test_closed_cycle_does_not_also_report_a_frozen_step(tmp_path):
    """The wrap-around step is zero by construction, not because anything froze."""
    volumes = moving_sphere(SMALL, 8)
    volumes[7] = volumes[0]
    report = build_report(_write_volumes(tmp_path / "STAN-0001A", volumes))
    assert not any("frozen at that step" in o for o in report["observations"])


def test_a_duplicate_in_the_middle_is_still_a_defect(tmp_path):
    """The closed-cycle exception is narrow: only first-equals-last qualifies."""
    volumes = moving_sphere(SMALL, 8)
    volumes[4] = volumes[2]
    report = build_report(_write_volumes(tmp_path / "STAN-0001A", volumes))

    assert report["closed_cycle"] is False
    assert report["has_motion"] is False
    assert any("phase sort collapsed" in o for o in report["observations"])


def test_two_duplicate_groups_are_not_a_closed_cycle(tmp_path):
    volumes = moving_sphere(SMALL, 8)
    volumes[7] = volumes[0]
    volumes[5] = volumes[2]
    report = build_report(_write_volumes(tmp_path / "STAN-0001A", volumes))
    assert report["closed_cycle"] is False
    assert any("phase sort collapsed" in o for o in report["observations"])


def test_an_all_identical_sequence_is_not_a_closed_cycle(tmp_path):
    static = np.repeat(moving_sphere(SMALL, 1), 5, axis=0)
    report = build_report(_write_volumes(tmp_path / "STAN-0001A", static))
    assert report["closed_cycle"] is False
    assert report["has_motion"] is False


def test_two_identical_timepoints_are_not_a_cycle():
    """A two-frame study where both frames match is degenerate, not cyclic."""
    from openh4d.motion_report import is_closed_cycle

    assert is_closed_cycle([[0, 1]], 2) is False
    assert is_closed_cycle([[0, 7]], 8) is True
    assert is_closed_cycle([], 8) is False
