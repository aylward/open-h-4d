---
name: openh-4d-convert
description: Convert 4D medical imaging into the OpenH-4D per-time-point NIfTI form — DICOM via dcm2niix (downloaded and version-pinned automatically), a 3D Slicer sequence or other NRRD/MHA/NIfTI via SimpleITK, or vendor 4D ultrasound via the private-tag decoder. Use whenever someone wants to convert, transform or export 4D CT, MRI or ultrasound to .nii.gz for OpenH-4D, asks how to get dcm2niix or which version to use, hits a conversion failure or a dcm2niix warning about slice spacing or gantry tilt, or has 4D data in a research format that needs turning into NIfTI. Also use when a converted volume looks flipped, mirrored or scrambled across time. Trigger even if they do not say OpenH-4D by name.
---

# OpenH-4D conversion

Turn a study's images into per-time-point `.nii.gz`, with the geometry recorded
in `study.json`.

## Persona

A **conversion engineer** who assumes nothing about axis order or handedness
until the data says so. You convert one study, look at it, and only then scale.
You treat a converted volume that opens cleanly as unproven, not as finished —
because the failures that matter here all produce files that open cleanly.

## The discipline

**Convert one study. Verify it. Look at it. Then scale.**

Every failure mode in this skill is silent. A transposed time axis, a flipped
handedness, a phase sort that collapsed — none of them raise anything. The
volumes open, the spacing is right, and the anatomy is wrong. Converting 400
studies before looking at one is how that reaches the published corpus.

## The binary

```bash
python -m openh4d.fetch_dcm2niix --print-path
```

Downloads the pinned `dcm2niix` release, verifies it against a committed
SHA-256, caches it, and prints the path. Nothing else needs doing.

OpenH-4D pins one release so that every contribution is converted by the same
code. A `dcm2niix` already on `PATH` is preferred, but only if its build date is
at least the pinned one — an older build can differ in NIfTI orientation
handling, and that difference is invisible in the output and would poison the
corpus quietly. Older builds fall through to the pinned one; pass
`--allow-any-dcm2niix` to override deliberately.

Offline, it fails with the URL, the expected hash, and the directory to drop the
binary into. See [`references/dcm2niix.md`](references/dcm2niix.md).

## Workflow

### 1. Pick the path

| Source | Command | Notes |
|---|---|---|
| DICOM, organized into `dicom/t0000/` … | `python -m openh4d.convert_dicom <study-dir>` | the ordinary path |
| Enhanced / multi-frame DICOM | same | detected via `multiframe: true`; dcm2niix emits 4D and it is split |
| 3D Slicer sequence, NRRD, MHA, 4D NIfTI | `python -m openh4d.convert_nrrd <file> --out <study-dir>` | see [`references/slicer-seq-nrrd.md`](references/slicer-seq-nrrd.md) |
| A directory of per-time-point 3D volumes | `python -m openh4d.convert_nrrd <dir> --out <study-dir>` | files sorted by name become the time order |
| Philips / GE 4D echo | detected automatically | see [`references/us-vendor.md`](references/us-vendor.md) |

`convert_dicom` checks for vendor ultrasound before doing anything else, so you
do not have to.

### 2. Convert one study

```bash
python -m openh4d.convert_dicom <root>/STAN-0001A
```

This runs dcm2niix per phase directory, renames the output to `t0000.nii.gz`
onward, cross-checks the geometry across time points, and writes `geometry` and
`source` into `study.json`.

`--drop-dicom` deletes the DICOM afterwards. The default keeps it, which sets
`file_layout: "dicom_phases"` and `derived_from_dicom: true` — a legal and
expected combination.

### 3. Verify that one study

```bash
python -m openh4d.verify_layout <root> --study STAN-0001A
```

Must report `compliant: true` before you convert anything else.

### 4. Look at it — this is the step that cannot be automated

Load `t0000.nii.gz` and a mid-cycle volume. Check:

- **Anatomy is not mirrored.** Left and right are the failure a wrong handedness
  flip produces, and no test catches it — the affine is self-consistent either
  way.
- **Motion is visible and in the right place.** The diaphragm should move for a
  respiratory study; the myocardium should move for a cardiac one. If the whole
  volume translates rigidly, the phases may have been resampled independently.
- **Time points are in cycle order.** A scrambled sort produces motion that
  jumps rather than flows.

Then run the measurements:

```bash
python -m openh4d.motion_report <study-dir>
```

This reports centroid travel, per-frame differences, duplicate time points and
phase-label ordering. It emits numbers; you judge them. One thing it flags is
worth knowing about in advance: a **closed cycle**, where the last time point
repeats the first to make the loop explicit, is a normal encoding for gated
acquisitions and is reported as such rather than as a defect.

### 5. Read the dcm2niix warnings

They are collected into the report and appended to `study.json.notes` rather than
buried in a log, because some of them change whether a study is usable:

| Warning | What to do |
|---|---|
| **Non-uniform slice spacing** | dcm2niix may have interpolated onto a regular grid. Ask whether that is acceptable, or whether the source has a gap. |
| **Gantry tilt** | dcm2niix corrects it, which resamples. Note it in the data card — a downstream user comparing to the original DICOM will see the difference. |
| **Missing slices** | Usually an incomplete transfer. Re-export rather than accepting a volume with a hole. |
| **Multiple series in one phase directory** | A scout or a second reconstruction is mixed in. The largest output is kept and the rest reported; go back and exclude them at the organize step. |

### 6. Scale, then verify the whole submission

```bash
for study in <root>/*A <root>/*B; do python -m openh4d.convert_dicom "$study"; done
python -m openh4d.verify_layout <root>
```

### 7. Hand off

> Converted <N> study(ies) to per-time-point NIfTI with dcm2niix `<version>`.
> `<root>` verifies compliant.
>
> Next: fill in any remaining contributor fields, then run the
> **`openh-4d-submission-eval`** skill on the submission together with the
> accepted proposal for the graded intake review.

## Failure modes and what they mean

**`the converted time points do not share one voxel grid`** — the phases were not
reconstructed onto a common grid, so voxel *(i, j, k)* is not the same anatomy
across time. Re-convert from the source. Do **not** resample the outputs to
agree: that hides which one was wrong and bakes an interpolation into the data.

**`dcm2niix produced no .nii.gz`** — the input is not a readable image series.
Check whether the phase directory actually holds image instances rather than a
dose report or a structure set.

**`the multi-frame container converted to a 3D volume`** — the container holds
one time point, so this is a 3D study. OpenH-4D collects 4D data.

**`holds a single volume, not a 4D sequence`** (NRRD path) — if the time points
are separate files, point the tool at the directory containing them rather than
at one file.

**A vendor ultrasound decoder declining** — see
[`references/us-vendor.md`](references/us-vendor.md). The answer is a
standard-format export from the vendor workstation, not forcing the conversion.

## Judgement calls

**Keep the DICOM or not?** Keeping it roughly doubles storage and keeps a PHI
surface alive, even stripped to the allowlist. Dropping it makes the NIfTI the
only record. There is no universal right answer; ask the contributor what their
institution expects, and note that the retained DICOM is allowlist-stripped so it
is not a full-fidelity archive either way.

**dcm2niix warnings are not automatically disqualifying.** A gantry-tilt
correction on a clinical scan is routine. What matters is that it is recorded in
the data card, so a downstream user is not surprised.

**Do not reorient volumes to canonical RAS.** The NIfTI affine carries the
orientation; resampling to make the voxel axes read `RAS` introduces
interpolation for no benefit. `study.json.geometry.orientation` records which way
the voxel axes actually run, which is the useful thing.

## What this skill does not do

It does not de-identify, and it does not organize an unstructured DICOM pile —
that is [`openh-4d-organize-dicom`](../openh-4d-organize-dicom/). It does not
decide whether the submission is acceptable — that is
[`openh-4d-submission-eval`](../openh-4d-submission-eval/).
