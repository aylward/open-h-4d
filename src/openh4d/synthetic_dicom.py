# SPDX-License-Identifier: Apache-2.0
"""Write a synthetic DICOM export, shaped like what a contributor hands over.

A flat pile of files with no directory structure, which is what comes off a PACS
export, so ``dicom_index`` has real grouping work to do. The pixel data is the
same moving sphere :mod:`openh4d.synthetic` uses, so the DICOM-to-NIfTI round
trip is checked against a volume whose correct content is known.

The temporal encoding is selectable, because the whole point of the tag ladder
in :mod:`openh4d.dicom_index` is that real exports encode time in different
places:

``temporal_position``
    ``TemporalPositionIdentifier`` on every instance -- one series.
``series_description``
    One series per time point, phase written into ``SeriesDescription`` as
    ``0.0%`` ... ``90.0%``. This is the TCIA 4D-Lung shape.
``trigger_time``
    ``TriggerTime`` per time point, as cardiac-gated CT produces.
``acquisition_number``
    ``AcquisitionNumber`` per time point.
``none``
    No temporal tag at all, so the ladder has to fail cleanly.

Usage::

    python -m openh4d.synthetic_dicom --out <dir> --encoding series_description
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from .synthetic import DEFAULT_SHAPE, DEFAULT_TIMEPOINTS, moving_sphere

ENCODINGS = (
    "temporal_position",
    "series_description",
    "trigger_time",
    "acquisition_number",
    "none",
)

DEFAULT_IN_PLANE_MM = 0.8
DEFAULT_SLICE_SPACING_MM = 1.0

CT_STORAGE = "1.2.840.10008.5.1.4.1.1.2"


def _uid(seed: str) -> str:
    """A deterministic, syntactically valid UID, so runs are reproducible."""
    import hashlib

    digest = hashlib.sha256(seed.encode()).hexdigest()
    parts = [str(int(digest[i : i + 6], 16) % 100000) for i in range(0, 24, 6)]
    return "1.2.826.0.1.3680043.10." + ".".join(parts)


def write_series(
    out: Path,
    *,
    patient_id: str = "SRC-PATIENT-001",
    patient_name: str = "ANON^ANON",
    study_seed: str = "study-1",
    n_timepoints: int = DEFAULT_TIMEPOINTS,
    shape: tuple[int, int, int] = DEFAULT_SHAPE,
    encoding: str = "series_description",
    in_plane_mm: float = DEFAULT_IN_PLANE_MM,
    slice_spacing_mm: float = DEFAULT_SLICE_SPACING_MM,
    include_phi: bool = False,
) -> Path:
    """Write one synthetic 4D study as a flat pile of ``.dcm`` files."""
    import pydicom
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian

    if encoding not in ENCODINGS:
        raise ValueError(f"unknown encoding {encoding!r}; expected one of {ENCODINGS}")

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    volumes = moving_sphere(shape, n_timepoints)
    nx, ny, nz = shape
    study_uid = _uid(f"{patient_id}/{study_seed}")
    instance_counter = 0

    for timepoint in range(n_timepoints):
        percent = timepoint * 100.0 / n_timepoints
        one_series_per_timepoint = encoding == "series_description"
        series_seed = f"{study_uid}/{timepoint}" if one_series_per_timepoint else study_uid
        series_uid = _uid(series_seed)

        for slice_index in range(nz):
            dataset = Dataset()
            dataset.file_meta = FileMetaDataset()
            dataset.file_meta.MediaStorageSOPClassUID = CT_STORAGE
            dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

            dataset.SOPClassUID = CT_STORAGE
            dataset.SOPInstanceUID = _uid(f"{series_uid}/{timepoint}/{slice_index}")
            dataset.file_meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID

            dataset.PatientID = patient_id
            dataset.PatientName = patient_name
            dataset.StudyInstanceUID = study_uid
            dataset.SeriesInstanceUID = series_uid
            dataset.Modality = "CT"
            dataset.SeriesNumber = timepoint + 1 if one_series_per_timepoint else 1
            instance_counter += 1
            dataset.InstanceNumber = instance_counter

            if include_phi:
                # Deliberately identified, for testing the pre-flight refusal.
                dataset.PatientName = "DOE^JANE"
                dataset.PatientBirthDate = "19620417"
                dataset.StudyDate = "20260914"
                dataset.InstitutionName = "General Hospital"
                dataset.ReferringPhysicianName = "SMITH^JOHN"
                dataset.AccessionNumber = "ACC1234567"

            dataset.Manufacturer = "OpenH-4D"
            dataset.ManufacturerModelName = "synthetic"
            dataset.PatientPosition = "HFS"

            dataset.Rows = ny
            dataset.Columns = nx
            dataset.PixelSpacing = [in_plane_mm, in_plane_mm]
            dataset.SliceThickness = slice_spacing_mm
            dataset.SpacingBetweenSlices = slice_spacing_mm
            dataset.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            dataset.ImagePositionPatient = [0.0, 0.0, slice_index * slice_spacing_mm]
            dataset.FrameOfReferenceUID = _uid(f"{study_uid}/for")

            dataset.SamplesPerPixel = 1
            dataset.PhotometricInterpretation = "MONOCHROME2"
            dataset.BitsAllocated = 16
            dataset.BitsStored = 16
            dataset.HighBit = 15
            dataset.PixelRepresentation = 1
            dataset.RescaleIntercept = 0
            dataset.RescaleSlope = 1

            _apply_temporal_encoding(
                dataset, encoding, timepoint, n_timepoints, percent, one_series_per_timepoint
            )

            # DICOM rows are the slow in-plane axis, so transpose out of (x, y).
            plane = np.ascontiguousarray(volumes[timepoint, :, :, slice_index].T)
            dataset.PixelData = plane.astype(np.int16).tobytes()

            # Names must be unique across every patient and study written into
            # the same directory: a real PACS export is one flat pile, and
            # colliding names would silently overwrite earlier studies.
            name = f"{study_uid[-12:]}_t{timepoint:02d}_s{slice_index:03d}.dcm"
            pydicom.dcmwrite(str(out / name), dataset, enforce_file_format=True)

    return out


def _apply_temporal_encoding(
    dataset,
    encoding: str,
    timepoint: int,
    n_timepoints: int,
    percent: float,
    one_series_per_timepoint: bool,
) -> None:
    if encoding == "temporal_position":
        dataset.TemporalPositionIdentifier = timepoint + 1
        dataset.NumberOfTemporalPositions = n_timepoints
        dataset.SeriesDescription = "4D CT"
    elif encoding == "series_description":
        dataset.SeriesDescription = f"4D CT Phase {percent:.1f}%"
    elif encoding == "trigger_time":
        dataset.TriggerTime = round(timepoint * 83.3, 1)
        dataset.CardiacNumberOfImages = n_timepoints
        dataset.SeriesDescription = "Cardiac 4D CT"
    elif encoding == "acquisition_number":
        dataset.AcquisitionNumber = timepoint + 1
        dataset.SeriesDescription = "4D CT"
    else:
        dataset.SeriesDescription = "4D CT"


def write_multi_patient_source(
    out: Path, *, n_patients: int = 2, n_timepoints: int = 4, shape=(8, 8, 4)
) -> Path:
    """A source with several patients, one of whom has two studies."""
    out = Path(out)
    for index in range(1, n_patients + 1):
        patient_id = f"SRC-PATIENT-{index:03d}"
        n_studies = 2 if index == 1 else 1
        for study in range(n_studies):
            write_series(
                out,
                patient_id=patient_id,
                study_seed=f"study-{study}",
                n_timepoints=n_timepoints,
                shape=shape,
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--encoding", choices=ENCODINGS, default="series_description")
    parser.add_argument("--timepoints", type=int, default=DEFAULT_TIMEPOINTS)
    parser.add_argument("--patients", type=int, default=1)
    parser.add_argument(
        "--size", type=int, nargs=3, metavar=("NX", "NY", "NZ"), default=list(DEFAULT_SHAPE)
    )
    parser.add_argument(
        "--include-phi",
        action="store_true",
        help="write identified headers, for testing the de-identification pre-flight",
    )
    args = parser.parse_args(argv)

    if args.patients > 1:
        out = write_multi_patient_source(
            args.out, n_patients=args.patients, n_timepoints=args.timepoints, shape=tuple(args.size)
        )
    else:
        out = write_series(
            args.out,
            encoding=args.encoding,
            n_timepoints=args.timepoints,
            shape=tuple(args.size),
            include_phi=args.include_phi,
        )
    n_files = len(list(Path(out).glob("*.dcm")))
    print(f"wrote {n_files} DICOM file(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
