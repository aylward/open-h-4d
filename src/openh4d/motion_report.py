# SPDX-License-Identifier: Apache-2.0
"""Measure whether a 4D study actually contains motion, and what kind.

This is the check unique to a 4D initiative. Everything else OpenH-4D verifies
would be just as true of a pile of 3D scans: the naming, the schemas, the
resolution thresholds. None of them notice a study where two "time points" are
the same volume copied twice, or where the phase sort collapsed, or where the
sequence never spans systole.

The division of labour is deliberate. This module emits **numbers**: a
displacement curve, a per-time-point intensity curve, duplicate detection, the
phase-label ordering. Whether the motion those numbers describe is physiologic
or an artefact is a judgement, and that belongs to the imaging physicist persona
in the submission evaluation, not to a threshold in here. The one thing the code
does assert is the case with no judgement in it: two time points that are
byte-for-byte identical are not two time points.

Usage::

    python -m openh4d.motion_report <study-dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

#: Below this fraction of the total intensity range, a study is effectively frozen.
FROZEN_RELATIVE_RANGE = 0.01

#: Centre-of-mass travel below this, in voxels, is indistinguishable from noise.
MINIMAL_CENTROID_TRAVEL_VOXELS = 0.25


def _timepoint_files(study_dir: Path) -> list[Path]:
    files = sorted(study_dir.glob("t[0-9][0-9][0-9][0-9].nii.gz"))
    if files:
        return files
    single = study_dir / "image4d.nii.gz"
    return [single] if single.is_file() else []


def load_series(study_dir: Path) -> tuple[np.ndarray, list[str]]:
    """Load a study's volumes as ``(t, x, y, z)`` float32, with their labels."""
    import nibabel as nib

    files = _timepoint_files(study_dir)
    if not files:
        raise FileNotFoundError(
            f"no volumes found in {study_dir}. Expected t0000.nii.gz ... or image4d.nii.gz."
        )

    if len(files) == 1 and files[0].name == "image4d.nii.gz":
        data = np.asanyarray(nib.load(str(files[0])).dataobj).astype(np.float32)
        if data.ndim < 4:
            raise ValueError(f"{files[0].name} is {data.ndim}D, so it holds no time axis")
        volumes = np.transpose(data, (3, 0, 1, 2))
        return volumes, [f"t{i:04d}" for i in range(volumes.shape[0])]

    volumes = np.stack(
        [np.asanyarray(nib.load(str(path)).dataobj).astype(np.float32) for path in files]
    )
    return volumes, [path.stem.replace(".nii", "") for path in files]


def intensity_curve(volumes: np.ndarray) -> list[float]:
    """Mean intensity per time point.

    A flat curve on its own is not a problem -- a rigid translation barely moves
    the mean. A curve with a discontinuity usually means one phase came from a
    different reconstruction.
    """
    return [float(volumes[t].mean()) for t in range(volumes.shape[0])]


def centroid_track(volumes: np.ndarray) -> list[list[float]]:
    """Intensity-weighted centre of mass per time point, in voxels.

    This is what actually detects bulk motion: a heart or diaphragm moving
    through the field of view shifts the centroid even when the mean intensity
    is unchanged.
    """
    n_timepoints = volumes.shape[0]
    grids = np.meshgrid(*[np.arange(n, dtype=np.float32) for n in volumes.shape[1:]], indexing="ij")
    track = []
    for t in range(n_timepoints):
        weights = volumes[t] - volumes[t].min()
        total = float(weights.sum())
        if total <= 0:
            track.append([float("nan")] * 3)
            continue
        track.append([float((grid * weights).sum() / total) for grid in grids])
    return track


def centroid_displacement(track: list[list[float]]) -> list[float]:
    """Distance of each time point's centroid from the first, in voxels."""
    origin = np.asarray(track[0], dtype=float)
    return [float(np.linalg.norm(np.asarray(point, dtype=float) - origin)) for point in track]


def find_duplicate_timepoints(volumes: np.ndarray) -> list[list[int]]:
    """Groups of time points that are byte-for-byte identical.

    The one unambiguous defect in this module. A duplicated phase means the sort
    collapsed or a file was copied, and no amount of imaging judgement makes two
    identical arrays into two observations.
    """
    n_timepoints = volumes.shape[0]
    groups: list[list[int]] = []
    assigned: set[int] = set()
    for i in range(n_timepoints):
        if i in assigned:
            continue
        group = [i]
        for j in range(i + 1, n_timepoints):
            if j not in assigned and np.array_equal(volumes[i], volumes[j]):
                group.append(j)
                assigned.add(j)
        if len(group) > 1:
            groups.append(group)
            assigned.add(i)
    return groups


def is_closed_cycle(duplicates: list[list[int]], n_timepoints: int) -> bool:
    """True when the only repeat is the last time point restating the first.

    A gated cardiac or respiratory sequence is often stored with the cycle
    closed: frame N-1 is a copy of frame 0, so the loop is explicit rather than
    implied. That is a normal encoding choice, not a collapsed phase sort, and
    treating it as a defect would flag a large share of real 4D data.

    The distinction is narrow on purpose. Exactly one duplicate group, containing
    exactly the first and last indices, is a closed cycle. Anything else --
    duplicates in the middle, three-way repeats, several groups -- is the failure
    this check exists to find.
    """
    if n_timepoints < 3 or len(duplicates) != 1:
        return False
    return duplicates[0] == [0, n_timepoints - 1]


def frame_differences(volumes: np.ndarray) -> list[float]:
    """Mean absolute difference between consecutive time points.

    A zero here with a non-zero elsewhere localizes exactly which phase froze.
    """
    return [float(np.abs(volumes[t + 1] - volumes[t]).mean()) for t in range(volumes.shape[0] - 1)]


def phase_label_order(study: dict[str, Any]) -> dict[str, Any]:
    """Whether the declared phase fractions increase monotonically.

    Non-monotonic phase labels mean the time points are not in acquisition order,
    which turns a smooth cycle into a scrambled one without changing any volume.
    """
    timepoints = study.get("timepoints")
    if not isinstance(timepoints, list):
        return {"checked": False}

    fractions = [tp.get("phase_fraction") for tp in timepoints if isinstance(tp, dict)]
    known = [f for f in fractions if isinstance(f, (int, float))]
    if len(known) < 2:
        return {"checked": False, "reason": "phase fractions are not recorded"}

    monotonic = all(b >= a for a, b in zip(known, known[1:]))
    return {
        "checked": True,
        "monotonic": monotonic,
        "fractions": known,
        "labels": [tp.get("phase_label") for tp in timepoints if isinstance(tp, dict)],
    }


def build_report(study_dir: Path) -> dict[str, Any]:
    """Measure one study. Emits numbers; the judgement is the reviewer's."""
    study_dir = Path(study_dir)
    volumes, labels = load_series(study_dir)

    study: dict[str, Any] = {}
    study_path = study_dir / "study.json"
    if study_path.is_file():
        try:
            loaded = json.loads(study_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                study = loaded
        except json.JSONDecodeError:
            study = {}

    intensities = intensity_curve(volumes)
    track = centroid_track(volumes)
    displacement = centroid_displacement(track)
    differences = frame_differences(volumes)
    duplicates = find_duplicate_timepoints(volumes)

    total_range = float(volumes.max() - volumes.min())
    intensity_span = float(max(intensities) - min(intensities))
    peak_travel = float(np.nanmax(displacement)) if displacement else 0.0

    n_timepoints = int(volumes.shape[0])
    closed_cycle = is_closed_cycle(duplicates, n_timepoints)

    observations: list[str] = []
    if closed_cycle:
        observations.append(
            f"the first and last time points are identical, so the sequence stores a closed "
            f"cycle: frame {n_timepoints - 1} repeats frame 0 to make the loop explicit. This is "
            f"normal for a gated cardiac or respiratory acquisition. The sequence carries "
            f"{n_timepoints - 1} distinct phases."
        )
    elif duplicates:
        observations.append(
            f"time points {duplicates} are byte-for-byte identical. Duplicated phases are not "
            f"two observations -- this usually means the phase sort collapsed or a file was "
            f"copied."
        )
    if peak_travel < MINIMAL_CENTROID_TRAVEL_VOXELS:
        observations.append(
            f"the centre of mass travels at most {peak_travel:.3f} voxel(s) across the whole "
            f"sequence, which is indistinguishable from noise. Confirm this study captures "
            f"motion at all."
        )
    # A flat intensity curve is entirely normal on its own -- a rigid translation
    # barely moves the mean -- so this is only worth saying when the centroid is
    # also still. Reported unconditionally it would fire on every healthy study
    # and train reviewers to ignore the whole report.
    flat_intensity = total_range > 0 and intensity_span / total_range < FROZEN_RELATIVE_RANGE
    if flat_intensity and peak_travel < MINIMAL_CENTROID_TRAVEL_VOXELS:
        observations.append(
            f"mean intensity varies by only {intensity_span:.4g} across a total range of "
            f"{total_range:.4g}, and the centre of mass barely moves. Together these mean the "
            f"sequence is effectively static."
        )
    for index, difference in enumerate(differences):
        if difference == 0.0 and not (closed_cycle and index == len(differences) - 1):
            observations.append(
                f"time points {index} and {index + 1} are identical, so the sequence is frozen "
                f"at that step."
            )

    order = phase_label_order(study)
    if order.get("checked") and not order.get("monotonic"):
        observations.append(
            f"declared phase fractions are not monotonic ({order['fractions']}). The time points "
            f"may not be in acquisition order, which scrambles the cycle without changing any "
            f"volume."
        )

    return {
        "study_dir": str(study_dir),
        "study_id": study.get("study_id", study_dir.name),
        "modality": study.get("modality"),
        "organ": study.get("organ"),
        "motion": study.get("motion"),
        "n_timepoints": int(volumes.shape[0]),
        "labels": labels,
        "intensity_curve": [round(v, 6) for v in intensities],
        "centroid_track_voxels": [[round(v, 4) for v in point] for point in track],
        "centroid_displacement_voxels": [round(v, 4) for v in displacement],
        "consecutive_frame_difference": [round(v, 6) for v in differences],
        "peak_centroid_travel_voxels": round(peak_travel, 4),
        "duplicate_timepoints": duplicates,
        "phase_label_order": order,
        "observations": observations,
        "closed_cycle": closed_cycle,
        "n_distinct_timepoints": n_timepoints - 1 if closed_cycle else n_timepoints,
        "has_motion": bool(
            peak_travel >= MINIMAL_CENTROID_TRAVEL_VOXELS and (closed_cycle or not duplicates)
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", type=Path, help="a study directory")
    args = parser.parse_args(argv)

    try:
        report = build_report(args.study_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2))
    for observation in report["observations"]:
        print(f"note: {observation}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
