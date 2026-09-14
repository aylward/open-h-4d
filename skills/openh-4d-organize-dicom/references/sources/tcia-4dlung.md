# TCIA 4D-Lung

Notes on the TCIA 4D-Lung collection, and on the release OpenH-4D uses as a
worked example.

## The OpenH-4D example release is NIfTI, not DICOM

`TCIA-4DLung-Part1.zip`, published by
[Project-MONAI/monai-physio](https://github.com/Project-MONAI/monai-physio), does
**not** contain DICOM. Verified by listing the archive: eight subject
directories, each holding ten `.nii.gz` volumes.

```
100_HM10395/
├── 100_HM10395_g000.nii.gz
├── 100_HM10395_g010.nii.gz
│   ...
└── 100_HM10395_g090.nii.gz
```

So `examples/tcia-4dlung/` uses `openh4d.convert_nrrd` on each subject directory,
not the DICOM path. If you are looking for a DICOM worked example, that is
`examples/synthetic-4d/`.

Measured geometry for subject `100_HM10395`: 0.9766 mm in plane, 3.0 mm slice
spacing, 512 × 512 × 142. Both within the RFP's CT thresholds for lung (1 mm
in-plane, 3 mm slices), the slice spacing exactly at the limit.

## The gate naming

`_g000` through `_g090` are respiratory gates: 0% through 90% of one breathing
cycle, where 0% is conventionally end-inhalation.

**That fraction is the only record of where in the cycle each volume sits.** The
converter numbers volumes `t0000` onward without knowing it, so the example reads
the gate out of the filename and writes it back as `phase_label` and
`phase_fraction`. Drop that step and you have ten volumes in an arbitrary order.

Note the ten gates do **not** close the cycle — there is no `g100` restating
`g000` — so this collection shows `closed_cycle: false`, unlike the TruncalValve
sequence.

## If you have the original DICOM

The upstream TCIA collection is DICOM, and a contributor working from it rather
than from the MONAI re-release will take the DICOM path. What to expect:

**One series per respiratory phase**, with the phase written into
`SeriesDescription`. The indexer's `SeriesDescription` rung handles this and
reports high confidence. Descriptions typically carry a percentage token that the
`(\d{1,3}(?:\.\d+)?)\s*%` pattern matches.

**Several studies per subject.** The collection includes repeat imaging across a
treatment course, so one subject legitimately has multiple 4D studies — which is
exactly what the letter suffix is for: `TCIA-0001A`, `TCIA-0001B`, and so on,
ordered by acquisition date.

**Non-image series mixed in.** Treatment-planning exports routinely carry RT
structure sets, dose reports and scouts. The indexer skips what it cannot read as
an image series and reports the count; review the unplaced list before applying.

## Clinical context

Lung cancer patients imaged for radiotherapy treatment planning. That is *why*
the acquisitions are 4D: the plan has to account for tumour motion with
respiration, so the internal target volume is defined across the breathing cycle.

Worth recording in `clinical_indication`, because it tells a downstream user
which population the data represents — a selected cohort with thoracic
malignancy, not a general one.

## What the release does not publish

No subject-level metadata: no age, sex, staging, vitals or treatment history. No
CT dose information.

The example leaves those `null` rather than guessing, and records the dose gap as
an explicit **waiver** against `ct_low_dose_declared` with a justification. That
is the intended pattern for a real gap: a waiver makes it visible to the steering
group, where a guess would make it invisible.

## Citation

Hugo, G. D., Weiss, E., Sleeman, W. C., Balik, S., Keall, P. J., Lu, J., &
Williamson, J. F. (2016). Data from 4D Lung Imaging of NSCLC Patients. The Cancer
Imaging Archive.
