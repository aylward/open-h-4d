# SPDX-License-Identifier: Apache-2.0
"""Convert a non-DICOM 4D volume into the OpenH-4D per-time-point NIfTI form.

Handles the sources contributors actually have when the data never went through
a PACS: a 3D Slicer sequence (``.seq.nrrd``), a plain 4D NRRD/MHA/NIfTI, or a
directory of per-time-point 3D volumes.

Two things about this path were established by reading a real file rather than
assumed, because both are silent failures if guessed wrong:

**The time axis comes first.** A Slicer sequence writes ``sizes: 21 512 391 218``
with ``kinds: list domain domain domain`` -- 21 time points of a 512x391x218
volume, time leading. SimpleITK reads that leading ``list`` axis as *vector
components* on a 3D image rather than as a fourth dimension, so the frames
arrive as the last axis of the array and ``GetDimension()`` reports 3.

**The handedness flips.** The file declares ``space: right-anterior-superior``,
but SimpleITK normalizes every image to LPS on read. NIfTI is RAS. So the affine
has to be flipped back, and getting that wrong mirrors the anatomy without
producing any error at all -- the volume opens, the geometry looks plausible,
and left and right are swapped.

Usage::

    python -m openh4d.convert_nrrd <file.seq.nrrd> --out <study-dir>
    python -m openh4d.convert_nrrd <dir-of-volumes> --out <study-dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

#: SimpleITK works in LPS; NIfTI is RAS. This flips x and y between them.
LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])

#: Extensions SimpleITK reads that make sense as a 4D or 3D medical volume.
READABLE_SUFFIXES = (".nrrd", ".nhdr", ".mha", ".mhd", ".nii", ".nii.gz", ".vtk")

#: Slicer records the sequence's index values here; they become phase labels.
SLICER_INDEX_VALUES_KEY = "axis 0 index values"
SLICER_INDEX_TYPE_KEY = "axis 0 index type"
SLICER_INDEX_UNIT_KEY = "axis 0 index unit"


class NrrdConvertError(RuntimeError):
    """The source could not be converted. The message says what to do about it."""


def affine_from_image(image) -> np.ndarray:
    """Build the RAS affine NIfTI wants from a SimpleITK image's LPS geometry."""
    direction = np.asarray(image.GetDirection(), dtype=float).reshape(3, 3)
    spacing = np.asarray(image.GetSpacing(), dtype=float)
    origin = np.asarray(image.GetOrigin(), dtype=float)

    lps = np.eye(4)
    lps[:3, :3] = direction @ np.diag(spacing)
    lps[:3, 3] = origin
    return LPS_TO_RAS @ lps


def read_source(path: Path):
    """Read a volume with SimpleITK, raising a useful message when it cannot."""
    import SimpleITK as sitk

    try:
        return sitk.ReadImage(str(path))
    except Exception as exc:  # noqa: BLE001 - SimpleITK raises a bare RuntimeError
        raise NrrdConvertError(
            f"SimpleITK could not read {path.name}: {exc}\n"
            f"Supported: {', '.join(READABLE_SUFFIXES)}."
        ) from exc


def split_timepoints(image) -> tuple[np.ndarray, int]:
    """Return ``(volumes, n_timepoints)`` with volumes shaped ``(t, x, y, z)``.

    Three shapes arrive here:

    * a 3D image with N components per voxel -- a Slicer sequence, N time points
    * a genuine 4D image -- time is the last SimpleITK dimension
    * a plain 3D image -- one time point, which the caller rejects as not 4D
    """
    import SimpleITK as sitk

    array = sitk.GetArrayFromImage(image)  # SimpleITK reverses the axis order

    if image.GetDimension() == 3 and image.GetNumberOfComponentsPerPixel() > 1:
        # (z, y, x, t) -> (t, x, y, z)
        return np.transpose(array, (3, 2, 1, 0)), array.shape[3]

    if image.GetDimension() == 4:
        # (t, z, y, x) -> (t, x, y, z)
        return np.transpose(array, (0, 3, 2, 1)), array.shape[0]

    if image.GetDimension() == 3:
        return np.transpose(array, (2, 1, 0))[np.newaxis, ...], 1

    raise NrrdConvertError(
        f"unsupported image dimension {image.GetDimension()} with "
        f"{image.GetNumberOfComponentsPerPixel()} component(s) per voxel"
    )


def sequence_index_values(image) -> list[str] | None:
    """The Slicer sequence index values, which become the phase labels."""
    keys = set(image.GetMetaDataKeys())
    if SLICER_INDEX_VALUES_KEY not in keys:
        return None
    raw = image.GetMetaData(SLICER_INDEX_VALUES_KEY).strip()
    return raw.split() or None


def sequence_index_unit(image) -> str | None:
    keys = set(image.GetMetaDataKeys())
    if SLICER_INDEX_UNIT_KEY in keys:
        return image.GetMetaData(SLICER_INDEX_UNIT_KEY).strip() or None
    return None


def write_timepoints(study_dir: Path, volumes: np.ndarray, affine: np.ndarray) -> list[Path]:
    """Write ``t0000.nii.gz`` and friends."""
    import nibabel as nib

    study_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(volumes.shape[0]):
        target = study_dir / f"t{index:04d}.nii.gz"
        volume = np.ascontiguousarray(volumes[index])
        nib.save(nib.Nifti1Image(volume, affine), str(target))
        written.append(target)
    return written


def _strip_suffixes(name: str) -> str:
    """Filename without its image extension.

    ``Path.stem`` only removes the last suffix, so a ``.nii.gz`` volume would be
    labelled ``patient_g000.nii``. These labels end up in ``study.json`` as the
    contributor's own phase names, so the trailing ``.nii`` is worth removing.
    """
    lowered = name.lower()
    for suffix in sorted(READABLE_SUFFIXES, key=len, reverse=True):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def _collect_directory(source: Path) -> list[Path]:
    """Per-time-point volumes in a directory, in a stable order."""
    candidates = [
        path
        for path in sorted(source.iterdir())
        if path.is_file() and any(path.name.lower().endswith(s) for s in READABLE_SUFFIXES)
    ]
    if not candidates:
        raise NrrdConvertError(
            f"no readable volumes in {source}. Expected files ending in "
            f"{', '.join(READABLE_SUFFIXES)}."
        )
    return candidates


def convert_directory(source: Path, study_dir: Path) -> tuple[list[Path], dict[str, Any]]:
    """Convert a directory of per-time-point 3D volumes."""
    import SimpleITK as sitk

    files = _collect_directory(source)
    first = read_source(files[0])
    affine = affine_from_image(first)
    reference_size = first.GetSize()

    stacked = []
    for path in files:
        image = read_source(path)
        if image.GetSize() != reference_size:
            raise NrrdConvertError(
                f"{path.name} has size {image.GetSize()} but {files[0].name} has "
                f"{reference_size}. Every time point of a study must share one voxel grid."
            )
        volumes, _ = split_timepoints(image)
        stacked.append(volumes[0])

    volumes = np.stack(stacked)
    written = write_timepoints(study_dir, volumes, affine)
    return written, {
        "source_files": [p.name for p in files],
        "spacing_mm": list(first.GetSpacing()),
        "labels": [_strip_suffixes(p.name) for p in files],
        "sitk_version": sitk.Version.VersionString(),
    }


def convert_file(source: Path, study_dir: Path) -> tuple[list[Path], dict[str, Any]]:
    """Convert a single 4D sequence file."""
    import SimpleITK as sitk

    image = read_source(source)
    volumes, n_timepoints = split_timepoints(image)

    if n_timepoints < 2:
        raise NrrdConvertError(
            f"{source.name} holds a single volume, not a 4D sequence. OpenH-4D collects 4D "
            f"data: a study needs at least two time points. If your time points are separate "
            f"files, point this tool at the directory containing them instead."
        )

    affine = affine_from_image(image)
    written = write_timepoints(study_dir, volumes, affine)

    labels = sequence_index_values(image)
    if labels and len(labels) != n_timepoints:
        labels = None

    return written, {
        "source_files": [source.name],
        "spacing_mm": list(image.GetSpacing()),
        "labels": labels,
        "index_unit": sequence_index_unit(image),
        # SimpleITK normalizes every image to LPS on read and rewrites this
        # metadata key to match, so it reports the space the data is in *now*,
        # not the one the file declared. Naming it "declared" would be wrong.
        "normalized_space": (
            image.GetMetaData("NRRD_space")
            if "NRRD_space" in set(image.GetMetaDataKeys())
            else None
        ),
        "sitk_version": sitk.Version.VersionString(),
    }


def update_study_json(
    study_dir: Path,
    volumes: list[Path],
    affine: np.ndarray,
    info: dict[str, Any],
    *,
    study_id: str | None = None,
    patient_id: str | None = None,
) -> dict[str, Any]:
    """Write or update the study sidecar with the conversion result."""
    import nibabel as nib

    from . import __version__
    from .check_volumes import read_header
    from .synthetic import utc_now

    path = study_dir / "study.json"
    study: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                study = loaded
        except json.JSONDecodeError:
            study = {}

    header = read_header(volumes[0])
    labels = info.get("labels")

    study.setdefault("schema", "openh-4d/study/1.0")
    study.setdefault("study_id", study_id or study_dir.name)
    if patient_id:
        study["patient_id"] = patient_id
    else:
        study.setdefault("patient_id", _infer_patient_id(study_dir.name))

    study.setdefault("modality", None)
    study.setdefault("organ", None)
    study.setdefault("motion", None)
    study.setdefault("coverage", None)
    study.setdefault("contrast", {"used": None, "agent": None, "phase": None})
    study.setdefault("annotations", {"segmentations": [], "landmarks": [], "categorizations": []})
    study.setdefault("derived", [])
    study.setdefault("waivers", [])

    study["file_layout"] = "timepoint_volumes"
    study["n_timepoints"] = len(volumes)
    study["timepoints"] = [
        {
            "index": index,
            "file": volume.name,
            "phase_label": labels[index] if labels else None,
            "phase_type": None,
            "phase_fraction": round(index / len(volumes), 6),
            "trigger_time_ms": None,
            "acquisition_time_s": None,
        }
        for index, volume in enumerate(volumes)
    ]
    study["geometry"] = {
        **study.get("geometry", {}),
        "in_plane_mm": [round(v, 6) for v in header.in_plane_mm],
        "slice_spacing_mm": round(header.slice_spacing_mm, 6),
        "matrix": list(header.spatial_shape),
        "orientation": "".join(nib.aff2axcodes(affine)),
    }
    study["source"] = {
        **study.get("source", {}),
        "original_format": (
            Path(info["source_files"][0])
            .name[len(_strip_suffixes(info["source_files"][0])) :]
            .lstrip(".")
            .upper()
            or "unknown"
        ),
        "converter": f"openh4d {__version__} convert_nrrd (SimpleITK {info['sitk_version']})",
        "converter_args": None,
        "converted_at": utc_now(),
        "n_source_files": len(info["source_files"]),
    }
    study.setdefault(
        "notes",
        "Converted by openh4d.convert_nrrd. Fields left null are not derivable from the source "
        "and must be supplied by the contributor: modality, organ, motion, coverage, contrast.",
    )

    path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    return study


def _infer_patient_id(study_dir_name: str) -> str | None:
    from .naming import InvalidNameError, parse_dir_name

    try:
        return parse_dir_name(study_dir_name).patient_id
    except InvalidNameError:
        return None


def convert(
    source: Path,
    study_dir: Path,
    *,
    study_id: str | None = None,
    patient_id: str | None = None,
) -> dict[str, Any]:
    """Convert ``source`` into ``study_dir`` and update its ``study.json``."""
    source = Path(source)
    study_dir = Path(study_dir)

    if not source.exists():
        raise NrrdConvertError(f"no such path: {source}")

    if source.is_dir():
        volumes, info = convert_directory(source, study_dir)
    else:
        volumes, info = convert_file(source, study_dir)

    if len(volumes) < 2:
        raise NrrdConvertError(
            f"only {len(volumes)} time point(s) were produced; OpenH-4D requires at least two."
        )

    from .check_volumes import read_header

    affine = read_header(volumes[0]).affine
    study = update_study_json(
        study_dir, volumes, affine, info, study_id=study_id, patient_id=patient_id
    )

    return {
        "source": str(source),
        "study_dir": str(study_dir),
        "n_timepoints": len(volumes),
        "files": [v.name for v in volumes],
        "geometry": study["geometry"],
        "phase_labels": info.get("labels"),
        "normalized_space": info.get("normalized_space"),
        "converter": study["source"]["converter"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="a 4D file, or a directory of 3D volumes")
    parser.add_argument("--out", type=Path, required=True, help="the study directory to write")
    parser.add_argument("--study-id", help="override the study identifier")
    parser.add_argument("--patient-id", help="override the patient identifier")
    args = parser.parse_args(argv)

    try:
        report = convert(args.source, args.out, study_id=args.study_id, patient_id=args.patient_id)
    except NrrdConvertError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2))
    print(
        "\nNext: fill in modality, organ, motion, coverage and contrast in "
        f"{args.out}/study.json, then verify the submission.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
