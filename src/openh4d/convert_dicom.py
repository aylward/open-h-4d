# SPDX-License-Identifier: Apache-2.0
"""Convert a study's DICOM into per-time-point NIfTI with dcm2niix.

Runs the pinned ``dcm2niix`` once per time-point directory, renames its output to
``t0000.nii.gz`` and so on, cross-checks the geometry across time points, and
writes what it learned back into ``study.json``.

Enhanced/multi-frame DICOM takes a different path: one container already holds
every time point, so dcm2niix is handed the whole directory and the resulting 4D
NIfTI is split into per-time-point files with nibabel.

Ultrasound is checked before anything else. Philips and GE 4D echo store the
volume in private tags that dcm2niix does not convert meaningfully, so those are
routed to :mod:`openh4d.us_vendor` rather than silently producing a file that
looks fine and is not.

Usage::

    python -m openh4d.convert_dicom <study-dir>
    python -m openh4d.convert_dicom <study-dir> --keep-dicom
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .check_volumes import VolumeReadError, compare_geometry, read_header
from .fetch_dcm2niix import Dcm2niixError, resolve

#: Arguments held constant for every OpenH-4D conversion, so 20,000 studies from
#: dozens of contributors are converted identically.
#:   -z y   gzip the output
#:   -b y   write the BIDS-style JSON sidecar, which carries acquisition detail
#:   -w 1   overwrite without prompting, so a re-run is not interactive
DCM2NIIX_ARGS = ["-z", "y", "-b", "y", "-w", "1"]

CONVERT_TIMEOUT_S = 600


class ConvertError(RuntimeError):
    """Conversion could not proceed. The message says what to do about it."""


def run_dcm2niix(
    binary: Path, source: Path, output_dir: Path, filename: str = "out"
) -> subprocess.CompletedProcess:
    """Run dcm2niix over one directory."""
    command = [
        str(binary),
        *DCM2NIIX_ARGS,
        "-f",
        filename,
        "-o",
        str(output_dir),
        str(source),
    ]
    return subprocess.run(command, capture_output=True, text=True, timeout=CONVERT_TIMEOUT_S)


def _pick_output(output_dir: Path) -> tuple[Path, Path | None]:
    """The converted volume and its sidecar, from whatever dcm2niix produced.

    dcm2niix splits a directory into several files when it finds series it
    considers distinct (different echoes, a scout, a localizer). For a single
    time point that means something unexpected was in the directory, so the
    largest output is taken and the rest are reported.
    """
    volumes = sorted(output_dir.glob("*.nii.gz"), key=lambda p: p.stat().st_size, reverse=True)
    if not volumes:
        raise ConvertError(
            f"dcm2niix produced no .nii.gz in {output_dir}. The input may not be a readable "
            f"image series."
        )
    chosen = volumes[0]
    sidecar = chosen.with_suffix("").with_suffix(".json")
    return chosen, sidecar if sidecar.is_file() else None


def convert_phase_dirs(
    study_dir: Path, binary: Path, *, keep_dicom: bool
) -> tuple[list[Path], list[str]]:
    """Convert ``dicom/t0000/`` and friends into ``t0000.nii.gz`` and friends."""
    dicom_root = study_dir / "dicom"
    phase_dirs = sorted(d for d in dicom_root.iterdir() if d.is_dir() and d.name.startswith("t"))
    if not phase_dirs:
        raise ConvertError(
            f"{dicom_root} has no per-phase subdirectories. If this is enhanced/multi-frame "
            f"DICOM, set multiframe true in study.json."
        )

    produced: list[Path] = []
    warnings: list[str] = []

    for phase_dir in phase_dirs:
        index = int(phase_dir.name[1:])
        with tempfile.TemporaryDirectory() as scratch:
            scratch_path = Path(scratch)
            result = run_dcm2niix(binary, phase_dir, scratch_path)
            if result.returncode != 0:
                raise ConvertError(
                    f"dcm2niix failed on {phase_dir.name} (exit {result.returncode}):\n"
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            warnings += _collect_warnings(result, phase_dir.name)

            volume, sidecar = _pick_output(scratch_path)
            extra = [p for p in scratch_path.glob("*.nii.gz") if p != volume]
            if extra:
                warnings.append(
                    f"{phase_dir.name}: dcm2niix split the input into {len(extra) + 1} volumes "
                    f"({', '.join(p.name for p in extra)}); the largest was kept. That usually "
                    f"means a scout or a second series is mixed into this time point."
                )

            target = study_dir / f"t{index:04d}.nii.gz"
            shutil.move(str(volume), str(target))
            if sidecar:
                shutil.move(str(sidecar), str(study_dir / f"t{index:04d}.json"))
            produced.append(target)

    if not keep_dicom:
        shutil.rmtree(dicom_root)

    return sorted(produced), warnings


def convert_multiframe(
    study_dir: Path, binary: Path, *, keep_dicom: bool
) -> tuple[list[Path], list[str]]:
    """Convert an enhanced/multi-frame container and split the result by time."""
    import nibabel as nib
    import numpy as np

    dicom_root = study_dir / "dicom"
    warnings: list[str] = []

    with tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch)
        result = run_dcm2niix(binary, dicom_root, scratch_path)
        if result.returncode != 0:
            raise ConvertError(
                f"dcm2niix failed on the multi-frame container (exit {result.returncode}):\n"
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        warnings += _collect_warnings(result, "multiframe")

        volume_path, sidecar = _pick_output(scratch_path)
        image = nib.load(str(volume_path))
        if image.ndim < 4:
            raise ConvertError(
                f"the multi-frame container converted to a {image.ndim}D volume with shape "
                f"{image.shape}. A 4D study must have a time axis -- check whether the "
                f"container really holds multiple time points."
            )

        data = np.asanyarray(image.dataobj)
        produced = []
        for index in range(data.shape[3]):
            target = study_dir / f"t{index:04d}.nii.gz"
            nib.save(nib.Nifti1Image(data[..., index], image.affine, image.header), str(target))
            produced.append(target)

        if sidecar:
            shutil.copy(str(sidecar), str(study_dir / "t0000.json"))

    if not keep_dicom:
        shutil.rmtree(dicom_root)

    return produced, warnings


#: dcm2niix messages worth surfacing rather than burying in a log.
NOTABLE_WARNINGS = (
    "slice",
    "gantry",
    "tilt",
    "missing",
    "interpolat",
    "non-uniform",
    "unsupported",
    "warning",
)


def _collect_warnings(result: subprocess.CompletedProcess, context: str) -> list[str]:
    output = f"{result.stdout}\n{result.stderr}"
    collected = []
    for line in output.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in NOTABLE_WARNINGS) and line.strip():
            collected.append(f"{context}: {line.strip()}")
    return collected


def detect_vendor_ultrasound(study_dir: Path) -> str | None:
    """Return a vendor name if this looks like private-tag 4D echo, else None."""
    from .us_vendor import detect

    dicom_root = study_dir / "dicom"
    if not dicom_root.is_dir():
        return None
    for path in sorted(dicom_root.rglob("*.dcm"))[:5]:
        vendor = detect(path)
        if vendor:
            return vendor
    return None


def update_study_json(
    study_dir: Path, volumes: list[Path], binary: Path, warnings: list[str]
) -> dict[str, Any]:
    """Write the conversion result back into ``study.json``."""
    from .fetch_dcm2niix import pinned_tag
    from .synthetic import utc_now

    path = study_dir / "study.json"
    if not path.is_file():
        raise ConvertError(f"{path} is missing; run organize_dicom first")

    study = json.loads(path.read_text(encoding="utf-8"))
    headers = []
    for volume in volumes:
        try:
            headers.append(read_header(volume))
        except VolumeReadError as exc:
            raise ConvertError(f"{volume.name} is unreadable after conversion: {exc}") from exc

    problems = compare_geometry(headers)
    if problems:
        raise ConvertError(
            "the converted time points do not share one voxel grid, so voxel (i, j, k) would "
            "not be the same anatomy across time:\n  " + "\n  ".join(p["message"] for p in problems)
        )

    reference = headers[0]
    study["geometry"] = {
        **study.get("geometry", {}),
        "in_plane_mm": [round(v, 6) for v in reference.in_plane_mm],
        "slice_spacing_mm": round(reference.slice_spacing_mm, 6),
        "matrix": list(reference.spatial_shape),
        "orientation": _orientation_code(reference),
    }
    study["n_timepoints"] = len(volumes)
    study["timepoints"] = _rebuild_timepoints(study, volumes)
    study["derived_from_dicom"] = (study_dir / "dicom").is_dir()
    study["file_layout"] = "dicom_phases" if study["derived_from_dicom"] else "timepoint_volumes"
    study["source"] = {
        **study.get("source", {}),
        "original_format": "DICOM",
        "converter": f"dcm2niix {pinned_tag()}",
        "converter_args": " ".join(DCM2NIIX_ARGS),
        "converted_at": utc_now(),
    }
    if warnings:
        existing = study.get("notes", "")
        study["notes"] = (existing + "\n\ndcm2niix warnings:\n- " + "\n- ".join(warnings)).strip()

    path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    return study


def _orientation_code(header) -> str:
    import nibabel as nib

    return "".join(nib.aff2axcodes(header.affine))


def _rebuild_timepoints(study: dict[str, Any], volumes: list[Path]) -> list[dict[str, Any]]:
    """Keep the phase labels the indexer recovered; update the filenames."""
    existing = {tp.get("index"): tp for tp in study.get("timepoints", []) if isinstance(tp, dict)}
    rebuilt = []
    for index, volume in enumerate(volumes):
        previous = existing.get(index, {})
        rebuilt.append(
            {
                "index": index,
                "file": volume.name,
                "phase_label": previous.get("phase_label"),
                "phase_type": previous.get("phase_type"),
                "phase_fraction": previous.get("phase_fraction"),
                "trigger_time_ms": previous.get("trigger_time_ms"),
                "acquisition_time_s": previous.get("acquisition_time_s"),
            }
        )
    return rebuilt


def convert_study(
    study_dir: Path,
    *,
    keep_dicom: bool = True,
    dcm2niix: Path | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Convert one study directory. Returns a report."""
    study_dir = Path(study_dir)
    if not (study_dir / "dicom").is_dir():
        raise ConvertError(
            f"{study_dir}/dicom does not exist. convert_dicom works on a study directory that "
            f"organize_dicom produced."
        )

    vendor = detect_vendor_ultrasound(study_dir)
    if vendor:
        from .us_vendor import convert_study as convert_vendor_study

        return convert_vendor_study(study_dir, vendor, keep_dicom=keep_dicom)

    try:
        binary = Path(dcm2niix) if dcm2niix else resolve(offline=offline)
    except Dcm2niixError as exc:
        raise ConvertError(str(exc)) from exc

    study_path = study_dir / "study.json"
    multiframe = False
    if study_path.is_file():
        try:
            multiframe = bool(json.loads(study_path.read_text(encoding="utf-8")).get("multiframe"))
        except json.JSONDecodeError:
            multiframe = False

    if multiframe:
        volumes, warnings = convert_multiframe(study_dir, binary, keep_dicom=keep_dicom)
    else:
        volumes, warnings = convert_phase_dirs(study_dir, binary, keep_dicom=keep_dicom)

    study = update_study_json(study_dir, volumes, binary, warnings)

    return {
        "study_dir": str(study_dir),
        "study_id": study.get("study_id"),
        "n_timepoints": len(volumes),
        "files": [v.name for v in volumes],
        "geometry": study["geometry"],
        "dcm2niix": str(binary),
        "warnings": warnings,
        "dicom_retained": keep_dicom,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", type=Path, help="a study directory containing dicom/")
    parser.add_argument(
        "--keep-dicom",
        action="store_true",
        default=True,
        help="keep the dicom/ tree alongside the NIfTI (the default)",
    )
    parser.add_argument(
        "--drop-dicom",
        dest="keep_dicom",
        action="store_false",
        help="delete dicom/ after a successful conversion",
    )
    parser.add_argument("--dcm2niix", type=Path, help="use this dcm2niix binary")
    parser.add_argument("--offline", action="store_true", help="never download dcm2niix")
    args = parser.parse_args(argv)

    try:
        report = convert_study(
            args.study_dir,
            keep_dicom=args.keep_dicom,
            dcm2niix=args.dcm2niix,
            offline=args.offline,
        )
    except ConvertError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2))
    for warning in report.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
