# SPDX-License-Identifier: Apache-2.0
"""Reading and sanity-checking the image volumes of a study.

Two tiers, deliberately separated by cost:

* :func:`read_header` opens a NIfTI lazily and returns shape, affine and voxel
  size without touching the pixel data. :mod:`openh4d.verify_layout` uses this
  on every run, so verification stays fast on multi-gigabyte submissions.
* :func:`check_volume` loads the array and looks for the ways a volume can be
  broken without being malformed: all-zero, constant, NaN or Inf, a truncated
  gzip stream. That is the image-integrity dimension of the submission
  evaluation, run deliberately rather than on every verification.

Run as ``python -m openh4d.check_volumes <study-dir-or-file>``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: Voxels smaller than this are almost certainly a unit error (metres for mm).
MIN_PLAUSIBLE_VOXEL_MM = 0.01
#: Voxels larger than this are not a diagnostic 4D CT/MR/US acquisition.
MAX_PLAUSIBLE_VOXEL_MM = 20.0

#: Affines must agree to this tolerance across the time points of one study.
AFFINE_TOLERANCE = 1e-4
#: Declared spacing must match the header to this tolerance, in millimetres.
SPACING_TOLERANCE_MM = 1e-3


@dataclass
class VolumeHeader:
    """Geometry of one image volume, read without loading pixel data."""

    path: Path
    shape: tuple[int, ...]
    affine: np.ndarray
    zooms: tuple[float, ...]
    dtype: str

    @property
    def in_plane_mm(self) -> tuple[float, float]:
        return (float(self.zooms[0]), float(self.zooms[1]))

    @property
    def slice_spacing_mm(self) -> float:
        return float(self.zooms[2])

    @property
    def spatial_shape(self) -> tuple[int, int, int]:
        return tuple(int(n) for n in self.shape[:3])  # type: ignore[return-value]

    @property
    def n_timepoints(self) -> int:
        """Length of the 4th dimension; 1 for a 3D volume."""
        return int(self.shape[3]) if len(self.shape) > 3 else 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "shape": list(self.shape),
            "zooms_mm": [round(float(z), 6) for z in self.zooms],
            "dtype": self.dtype,
        }


@dataclass
class VolumeReport:
    """Result of a full integrity check on one volume."""

    path: Path
    ok: bool = True
    checks: dict[str, Any] = field(default_factory=dict)
    problems: list[dict[str, str]] = field(default_factory=list)

    def fail(self, code: str, message: str) -> None:
        self.ok = False
        self.problems.append({"code": code, "message": message})

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "checks": self.checks,
            "problems": self.problems,
        }


class VolumeReadError(RuntimeError):
    """A volume could not be opened or decoded."""


def read_header(path: Path) -> VolumeHeader:
    """Read geometry only. Raises :class:`VolumeReadError` if the file is unusable."""
    import nibabel as nib

    try:
        image = nib.load(str(path))
        header = image.header
        return VolumeHeader(
            path=path,
            shape=tuple(int(n) for n in image.shape),
            affine=np.asarray(image.affine, dtype=float),
            zooms=tuple(float(z) for z in header.get_zooms()),
            dtype=str(header.get_data_dtype()),
        )
    except Exception as exc:  # noqa: BLE001 - any read failure is one finding
        raise VolumeReadError(f"{type(exc).__name__}: {exc}") from exc


def check_volume(path: Path) -> VolumeReport:
    """Load ``path`` and look for the ways a volume can be broken.

    A truncated gzip stream surfaces here rather than at header-read time,
    because the NIfTI header sits at the front of the file and decodes fine even
    when the image data behind it was cut off mid-transfer.
    """
    report = VolumeReport(path=path)

    try:
        header = read_header(path)
    except VolumeReadError as exc:
        report.fail("E_VOLUME_UNREADABLE", str(exc))
        return report

    report.checks["shape"] = list(header.shape)
    report.checks["dtype"] = header.dtype
    report.checks["zooms_mm"] = [round(z, 6) for z in header.zooms]

    if len(header.shape) < 3:
        report.fail(
            "E_VOLUME_NOT_3D",
            f"expected a 3D or 4D volume, got shape {header.shape}",
        )
        return report

    spatial_zooms = header.zooms[:3]
    if any(z <= 0 for z in spatial_zooms):
        report.fail("E_VOXEL_SIZE_NONPOSITIVE", f"voxel size {spatial_zooms} has a non-positive")
    elif any(z < MIN_PLAUSIBLE_VOXEL_MM or z > MAX_PLAUSIBLE_VOXEL_MM for z in spatial_zooms):
        report.fail(
            "E_VOXEL_SIZE_IMPLAUSIBLE",
            f"voxel size {spatial_zooms} mm is outside {MIN_PLAUSIBLE_VOXEL_MM}-"
            f"{MAX_PLAUSIBLE_VOXEL_MM} mm; this is usually a unit error (metres for millimetres)",
        )

    if abs(float(np.linalg.det(header.affine[:3, :3]))) < 1e-12:
        report.fail(
            "E_AFFINE_SINGULAR",
            "the affine has zero determinant, so voxel coordinates do not map to physical space",
        )

    import nibabel as nib

    try:
        data = np.asanyarray(nib.load(str(path)).dataobj)
    except Exception as exc:  # noqa: BLE001
        report.fail(
            "E_VOLUME_DATA_UNREADABLE",
            f"the header parsed but the image data did not decode ({type(exc).__name__}: {exc}). "
            f"A truncated gzip stream from an interrupted transfer looks exactly like this.",
        )
        return report

    finite = np.isfinite(data)
    n_nonfinite = int(data.size - finite.sum())
    report.checks["n_nonfinite"] = n_nonfinite
    if n_nonfinite:
        report.fail(
            "E_VOLUME_NONFINITE",
            f"{n_nonfinite} of {data.size} voxels are NaN or infinite",
        )

    finite_data = data[finite] if n_nonfinite else data
    if finite_data.size:
        vmin, vmax = float(finite_data.min()), float(finite_data.max())
        report.checks["min"] = vmin
        report.checks["max"] = vmax
        if vmin == vmax:
            report.fail(
                "E_VOLUME_CONSTANT",
                f"every voxel has the same value ({vmin}); the volume carries no image",
            )
    else:
        report.fail("E_VOLUME_EMPTY", "the volume contains no finite values")

    return report


def compare_geometry(headers: list[VolumeHeader]) -> list[dict[str, str]]:
    """Every time point of a study must share one shape and one affine.

    A drift here means the time points were not acquired or resampled onto a
    common grid, so voxel *(i, j, k)* is not the same anatomy across time --
    which silently breaks every downstream motion model.
    """
    problems: list[dict[str, str]] = []
    if len(headers) < 2:
        return problems

    reference = headers[0]
    for header in headers[1:]:
        if header.spatial_shape != reference.spatial_shape:
            problems.append(
                {
                    "code": "E_SHAPE_INCONSISTENT",
                    "message": (
                        f"{header.path.name} has shape {header.spatial_shape} but "
                        f"{reference.path.name} has {reference.spatial_shape}; every time point "
                        f"of a study must share one voxel grid"
                    ),
                }
            )
        if not np.allclose(header.affine, reference.affine, atol=AFFINE_TOLERANCE):
            problems.append(
                {
                    "code": "E_AFFINE_INCONSISTENT",
                    "message": (
                        f"{header.path.name} has a different affine from "
                        f"{reference.path.name}; voxel (i, j, k) would not be the same anatomy "
                        f"across time"
                    ),
                }
            )
    return problems


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="a NIfTI file, or a study directory")
    args = parser.parse_args(argv)

    target: Path = args.target
    if target.is_dir():
        files = sorted(target.glob("t[0-9][0-9][0-9][0-9].nii.gz"))
        files += sorted(target.glob("image4d.nii.gz"))
    else:
        files = [target]

    if not files:
        print(
            json.dumps(
                {
                    "target": str(target),
                    "ok": False,
                    "volumes": [],
                    "problems": [
                        {
                            "code": "E_NO_VOLUMES",
                            "message": f"no NIfTI volumes found under {target}",
                        }
                    ],
                },
                indent=2,
            )
        )
        return 1

    reports = [check_volume(path) for path in files]
    headers = []
    for path in files:
        try:
            headers.append(read_header(path))
        except VolumeReadError:
            pass
    geometry_problems = compare_geometry(headers)

    ok = all(r.ok for r in reports) and not geometry_problems
    print(
        json.dumps(
            {
                "target": str(target),
                "ok": ok,
                "volumes": [r.as_dict() for r in reports],
                "problems": geometry_problems,
            },
            indent=2,
        )
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    sys.exit(main())
