# SPDX-License-Identifier: Apache-2.0
"""Decode vendor 4D ultrasound DICOM.

Philips and GE store 4D echo volumes in private tag blocks that ``dcm2niix``
does not convert meaningfully. Handing such a file to the normal DICOM path
produces a NIfTI that opens cleanly and is geometrically wrong, which is worse
than failing, so this module intercepts it.

The structure is deliberate:

* :func:`detect` recognises the vendor from the private creator string.
* A decoder is dispatched by vendor and returns per-time-point volumes.
* :func:`self_check` gates every decoder's output. A decoder that cannot produce
  a plausible voxel size, a non-degenerate affine, and a volume that actually
  varies **declines** rather than emitting something it is not confident in.
* When no decoder matches, or one declines, the contributor gets an actionable
  message telling them to export a standard format from their vendor
  workstation and re-run through :mod:`openh4d.convert_nrrd`.

**Status.** The detection, dispatch, self-check and fallback are complete and
tested. The two vendor decoder bodies are not: neither of the OpenH-4D example
datasets contains vendor 4D echo, so there are no real bytes to build against,
and a decoder written against a guessed layout would be exactly the silent
wrongness this module exists to prevent. Each raises
:class:`DecoderNotImplemented`, which the fallback turns into the export
instruction. Finishing them needs one real Philips and one real GE 4D echo
study; see ``skills/openh-4d-convert/references/us-vendor.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

VENDOR_PHILIPS = "philips"
VENDOR_GE_KRETZ = "ge_kretz"

#: Private creator strings that identify a vendor 4D echo block. Matched
#: case-insensitively as a substring, because vendors pad and version these.
PRIVATE_CREATOR_MARKERS = {
    VENDOR_PHILIPS: (
        "philips us imaging dd",
        "philips imaging dd",
        "philips3d",
    ),
    VENDOR_GE_KRETZ: (
        "kretz",
        "ge kretz us",
        "ge_kretz",
    ),
}

#: A 4D echo voxel outside this range means the geometry was misread.
MIN_VOXEL_MM = 0.05
MAX_VOXEL_MM = 5.0

DECODER_VERSION = "0.1.0"


class UltrasoundVendorError(RuntimeError):
    """A vendor ultrasound study could not be converted. The message says what to do."""


class DecoderNotImplemented(UltrasoundVendorError):
    """A vendor was recognised but its decoder is not finished."""


@dataclass
class DecodedSeries:
    """What a decoder returns: the volumes plus the geometry to write them with."""

    volumes: np.ndarray
    """``(n_timepoints, nx, ny, nz)``."""

    voxel_mm: tuple[float, float, float]
    origin_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    probe: str | None = None
    frame_rate_hz: float | None = None
    decoder: str = "unknown"

    def affine(self) -> np.ndarray:
        affine = np.eye(4)
        for axis in range(3):
            affine[axis, axis] = self.voxel_mm[axis]
            affine[axis, 3] = self.origin_mm[axis]
        return affine


def detect(path: Path) -> str | None:
    """Return the vendor key if ``path`` is vendor 4D echo, else ``None``."""
    import pydicom
    from pydicom.errors import InvalidDicomError

    try:
        dataset = pydicom.dcmread(str(path), stop_before_pixels=True)
    except (InvalidDicomError, OSError):
        return None
    except Exception:  # noqa: BLE001
        return None

    if str(dataset.get("Modality", "")).upper() != "US":
        return None

    creators = []
    for element in dataset:
        if element.tag.is_private_creator and element.value:
            creators.append(str(element.value).strip().lower())

    for vendor, markers in PRIVATE_CREATOR_MARKERS.items():
        for creator in creators:
            if any(marker in creator for marker in markers):
                return vendor

    return None


def self_check(decoded: DecodedSeries) -> list[str]:
    """Reasons ``decoded`` must not be trusted. Empty means it passed.

    This is the gate that separates "we decoded it" from "we produced bytes".
    """
    problems: list[str] = []

    if decoded.volumes.ndim != 4:
        problems.append(f"expected a 4D array (time, x, y, z), got shape {decoded.volumes.shape}")
        return problems

    if decoded.volumes.shape[0] < 2:
        problems.append(
            f"only {decoded.volumes.shape[0]} time point(s) decoded; a 4D study needs at least 2"
        )

    if any(not np.isfinite(v) or v <= 0 for v in decoded.voxel_mm):
        problems.append(f"voxel size {decoded.voxel_mm} is not positive and finite")
    elif any(v < MIN_VOXEL_MM or v > MAX_VOXEL_MM for v in decoded.voxel_mm):
        problems.append(
            f"voxel size {decoded.voxel_mm} mm is outside the plausible range for 4D echo "
            f"({MIN_VOXEL_MM}-{MAX_VOXEL_MM} mm), which means the geometry was misread"
        )

    if abs(float(np.linalg.det(decoded.affine()[:3, :3]))) < 1e-12:
        problems.append("the affine is degenerate, so voxels do not map to physical space")

    finite = np.isfinite(decoded.volumes)
    if not finite.all():
        problems.append("the decoded volumes contain NaN or infinite values")
    elif float(decoded.volumes.min()) == float(decoded.volumes.max()):
        problems.append("every voxel has the same value, so nothing was actually decoded")

    return problems


# --- vendor decoders ----------------------------------------------------------


def decode_philips(path: Path) -> DecodedSeries:
    """Decode a Philips 4D echo Cartesian volume block (private group 200D).

    Not implemented: see the module docstring. Needs one real Philips 4D TEE or
    transthoracic study to build against.
    """
    raise DecoderNotImplemented(
        "the Philips 4D echo decoder is not finished. It needs a real Philips 4D study to be "
        "written against -- decoding a private tag layout from documentation alone produces "
        "volumes that look right and are not."
    )


def decode_ge_kretz(path: Path) -> DecodedSeries:
    """Decode a GE Kretz / Voluson ``.vol`` payload into Cartesian volumes.

    Not implemented: see the module docstring. The Kretz format stores a
    toroidal voxel array that must be resampled to Cartesian, and getting that
    resampling wrong distorts anatomy without producing any error.
    """
    raise DecoderNotImplemented(
        "the GE Kretz/Voluson decoder is not finished. It needs a real Voluson 4D study to be "
        "written against -- the toroidal-to-Cartesian resampling cannot be validated without "
        "one."
    )


DECODERS: dict[str, Callable[[Path], DecodedSeries]] = {
    VENDOR_PHILIPS: decode_philips,
    VENDOR_GE_KRETZ: decode_ge_kretz,
}

EXPORT_INSTRUCTION = (
    "Export this study from your vendor workstation in a standard format and convert that "
    "instead:\n"
    "  - Cartesian ('DICOM 3D', 'Cartesian volume') DICOM, or\n"
    "  - NRRD / MHD / NIfTI, one file per time point or one 4D sequence file\n"
    "Then run: python -m openh4d.convert_nrrd <file> --out <study-dir>\n"
    "A vendor-proprietary 4D echo file converted by a general DICOM tool produces a volume "
    "with the wrong geometry, which is worse than no volume at all."
)


def decode(path: Path, vendor: str | None = None) -> DecodedSeries:
    """Decode one vendor 4D echo file, or raise with an actionable message."""
    vendor = vendor or detect(path)
    if vendor is None:
        raise UltrasoundVendorError(
            f"{path.name} is not a recognised vendor 4D ultrasound file.\n\n{EXPORT_INSTRUCTION}"
        )

    decoder = DECODERS.get(vendor)
    if decoder is None:
        raise UltrasoundVendorError(f"no decoder for vendor {vendor!r}.\n\n{EXPORT_INSTRUCTION}")

    decoded = decoder(path)
    decoded.decoder = f"{vendor} {DECODER_VERSION}"

    problems = self_check(decoded)
    if problems:
        raise UltrasoundVendorError(
            f"the {vendor} decoder produced output it cannot vouch for:\n  "
            + "\n  ".join(problems)
            + f"\n\n{EXPORT_INSTRUCTION}"
        )
    return decoded


def write_decoded(study_dir: Path, decoded: DecodedSeries) -> list[Path]:
    """Write per-time-point NIfTI from a decoded series."""
    import nibabel as nib

    affine = decoded.affine()
    written = []
    for index in range(decoded.volumes.shape[0]):
        target = study_dir / f"t{index:04d}.nii.gz"
        nib.save(nib.Nifti1Image(decoded.volumes[index], affine), str(target))
        written.append(target)
    return written


def convert_study(study_dir: Path, vendor: str, *, keep_dicom: bool = True) -> dict[str, Any]:
    """Convert a study whose ``dicom/`` holds vendor 4D echo."""
    study_dir = Path(study_dir)
    candidates = sorted((study_dir / "dicom").rglob("*.dcm"))
    if not candidates:
        raise UltrasoundVendorError(f"no DICOM files under {study_dir}/dicom")

    decoded = decode(candidates[0], vendor)
    volumes = write_decoded(study_dir, decoded)

    study_path = study_dir / "study.json"
    if study_path.is_file():
        study = json.loads(study_path.read_text(encoding="utf-8"))
        study["modality"] = "US"
        study.setdefault("us", {})
        study["us"]["vendor_decoder"] = decoded.decoder
        study["us"]["probe"] = decoded.probe
        study["us"]["frame_rate_hz"] = decoded.frame_rate_hz
        study["n_timepoints"] = len(volumes)
        study["geometry"] = {
            **study.get("geometry", {}),
            "in_plane_mm": [decoded.voxel_mm[0], decoded.voxel_mm[1]],
            "slice_spacing_mm": decoded.voxel_mm[2],
            "matrix": list(decoded.volumes.shape[1:]),
        }
        study_path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")

    if not keep_dicom:
        import shutil

        shutil.rmtree(study_dir / "dicom")

    return {
        "study_dir": str(study_dir),
        "vendor": vendor,
        "decoder": decoded.decoder,
        "n_timepoints": len(volumes),
        "files": [v.name for v in volumes],
        "dicom_retained": keep_dicom,
    }
