# Fixing OpenH-4D verification failures

Every error and warning code the verifier emits, what it means, and what to do.

Codes beginning `E_` block acceptance. Codes beginning `W_` do not.

---

## Naming and structure

### `E_INVALID_DIR_NAME`

A directory at the submission root does not match the naming grammar.

The message names the likely cause. The three common ones:

- **A bare patient identifier** (`STAN-0001` with no letter). Every study
  directory carries a letter suffix starting at `A`, even when the patient has
  only one study. Rename to `STAN-0001A`.
- **Lowercase.** Directory names are uppercase, so they cannot collide on the
  case-insensitive filesystems Windows and macOS use.
- **A patient identifier ending in a letter.** Identifiers must end in a digit —
  that is what makes `STAN-001AB` parse one way instead of two.

### `E_STUDY_SUFFIX_GAP`

A patient's study letters are not contiguous from `A` — for example `…A` and `…C`
with no `…B`.

Almost always a directory lost in transfer. Check the source before renaming: if
`…B` exists and did not copy, copy it. Only renumber once you have confirmed the
missing study does not exist.

### `E_CASE_COLLISION`

Two directory names differ only in case. They coexist on Linux and collide on
Windows and macOS. Rename one.

### `E_NO_STUDIES`

No study directories under the root. Either the path is wrong, or the study
directories are one level deeper than you think — the root is the directory that
directly contains `STAN-0001A/` and `README.md`.

### `W_UNEXPECTED_ROOT_ENTRY`

A file at the root that is not `README.md`, `LICENSE`, `manifest.json` or
`checksums.sha256`. Harmless, but move scratch files out; a submission root
should be legible at a glance.

---

## Required files

### `E_MISSING_README`

`README.md` is the data card, and Hugging Face renders it as the dataset landing
page. Start from
[`../../openh-4d-shared/data-card-template.md`](../../openh-4d-shared/data-card-template.md).

### `E_MISSING_LICENSE`

OpenH-4D requires **CC BY 4.0** for the data. Put the licence text in `LICENSE`
at the root, and `license: cc-by-4.0` in the `README.md` frontmatter — the
frontmatter line is what drives the Hugging Face UI.

### `E_MISSING_MANIFEST`

```bash
python -m openh4d.manifest <submission-root>
```

Regenerate it after any change to the tree; it records counts and sidecar hashes.

### `E_MISSING_EHR_DIR` / `E_MISSING_PATIENT_JSON`

Every patient needs a `<PATIENT_ID>_ehr/patient.json` carrying the demographics,
disease state, vitals and reason for scan the RFP asks for. Copy
[`../../openh-4d-shared/patient-json-template.json`](../../openh-4d-shared/patient-json-template.json).

### `E_MISSING_STUDY_JSON`

Every study directory needs a `study.json`. Copy
[`../../openh-4d-shared/study-json-template.json`](../../openh-4d-shared/study-json-template.json).

### `E_EHR_WITHOUT_STUDIES`

An `_ehr` directory exists for a patient with no study directories. Either the
studies are missing, or the `_ehr` directory is a leftover.

---

## Sidecar contents

### `E_SCHEMA`

A JSON sidecar violates its schema. The message quotes the exact violation and
the field path.

The commonest case by far is a `study.json` straight out of `organize_dicom`,
where `organ`, `motion` and `coverage` are still `null`. That is deliberate:
nothing in a DICOM header says whether you scanned a heart or a lung, and the
tooling will not guess. Fill them in.

### `E_STUDY_ID_MISMATCH` / `E_PATIENT_ID_MISMATCH`

The identifier inside the JSON disagrees with the directory name. Usually a
copy-paste when a study was duplicated as a starting point. The directory name
is normally the one to trust — but check, because the other possibility is that
the whole directory was misnamed.

### `E_PATIENT_STUDIES_MISSING` / `E_PATIENT_STUDIES_UNLISTED`

`patient.json.studies` does not match the study directories on disk. Missing
means it lists a study that is not there; unlisted means a study is there and not
listed. Reconcile the two.

### `E_TIMEPOINT_COUNT_MISMATCH`

`n_timepoints` does not match the number of image files present. Recount; do not
just edit the number until the error goes away — if a time point is genuinely
missing, that is the actual problem.

### `E_TIMEPOINT_INDEX_NOT_CONTIGUOUS`

The `timepoints` array in `study.json` is not indexed `0, 1, 2, …`.

### `E_AGE_OVER_89_NOT_SUPPRESSED` / `E_AGE_MISSING`

An exact age above 89 is a HIPAA Safe Harbor identifier. Report it as:

```json
"demographics": {"age_years": null, "age_90_or_older": true}
```

`E_AGE_MISSING` means neither form is populated — no age at all is reported.

### `E_DATE_IN_METADATA`

A calendar date appears in a field where one does not belong. Dates are Safe
Harbor identifiers. Use an offset relative to the scan instead —
`treatment_history[].days_relative_to_scan` is a signed integer for exactly this.

Dates under `deidentification` are allowed, since describing a date shift
legitimately mentions dates.

---

## Image data

### `E_NO_IMAGE_DATA`

No volumes found. A study directory holds one of:

- `t0000.nii.gz`, `t0001.nii.gz`, … (canonical)
- `dicom/t0000/`, `dicom/t0001/`, … (canonical)
- `image4d.nii.gz` (accepted fallback, exactly that filename)

### `E_MIXED_LAYOUT`

More than one image form in one study directory. The single legal combination is
a retained `dicom/` tree alongside converted per-time-point NIfTI, which requires
`file_layout: "dicom_phases"` and `derived_from_dicom: true` in `study.json`.
That is the normal output of organize-then-convert.

### `E_LAYOUT_MISMATCH`

`file_layout` in `study.json` does not describe what is actually on disk.

### `E_TIMEPOINT_GAP`

Time-point files or phase directories are not numbered contiguously from `0000`.
A gap almost always means a file was lost in transfer. Check the source before
renumbering.

### `E_EMPTY_PHASE_DIR`

A `dicom/t####/` directory has no files in it.

### `E_MULTIFRAME_WITH_PHASE_DIRS`

`study.json` says `multiframe: true` but `dicom/` has per-phase subdirectories.
An enhanced/multi-frame DICOM container already holds every time point, so its
files go directly in `dicom/` with no subdirectories.

### `E_SINGLE_4D_NOT_4D`

`image4d.nii.gz` is 3D. The single-file form must carry time as the fourth
dimension.

### `E_VOLUME_UNREADABLE`

A NIfTI file did not open. Usually a truncated transfer. Re-copy it, then run
`python -m openh4d.check_volumes <study-dir>` to check the rest.

---

## Geometry

### `E_GEOMETRY_MISMATCH`

`study.json` declares a spacing or matrix that the NIfTI header contradicts.

This matters because the RFP resolution thresholds are checked against the
declared values, so the two must agree or the check means nothing. If the header
is right and the JSON is stale, backfill it from the header. If the header is
wrong, the conversion needs redoing.

### `E_SHAPE_INCONSISTENT` / `E_AFFINE_INCONSISTENT`

Time points do not share one voxel grid, so voxel *(i, j, k)* is not the same
anatomy across time. This silently breaks every downstream motion model.

Usually one of: volumes from two different reconstructions mixed into one study,
or a phase that was resampled and the others were not. Re-convert from the
source rather than resampling the outputs — resampling to agree hides which one
was wrong.

---

## RFP conformance

### `E_CONFORMANCE`

An RFP acquisition threshold is not met. The message names the rule:

| Rule | Requirement |
|---|---|
| `ct_in_plane_resolution` | CT in-plane 1.0 mm or less |
| `ct_slice_spacing_cardiac` | cardiac CT slice spacing 2.0 mm or less |
| `ct_slice_spacing_lung` | lung CT slice spacing 3.0 mm or less |
| `ct_low_dose_declared` | CT must state whether it was low dose |
| `mr_cine_resolution_declared` | MR must state in-plane and temporal resolution |
| `us_source_declared` | ultrasound must state `4d_tee`, `4d_percutaneous` or `other` |
| `min_timepoints` | at least 2 time points |

Two ways forward. If the information is simply missing, supply it. If the data
genuinely falls outside a threshold and you want it considered anyway, record a
**waiver**:

```json
"waivers": [
  {"rule": "ct_slice_spacing_cardiac",
   "justification": "Legacy scanner; thinner reconstruction is not available for this cohort."}
]
```

A waiver downgrades the rule to `W_CONFORMANCE` so the steering group sees it and
decides. It is not a way to silence a check — an unjustified waiver is worse than
the original finding.

**`min_timepoints` cannot be waived.** A single time point is a 3D study.

### `E_UNKNOWN_WAIVER`

A waiver names a rule that does not exist — almost always a typo, which would
otherwise silently do nothing. The message lists the valid rule ids.

### `E_UNWAIVABLE_WAIVER`

A waiver names `min_timepoints`, which cannot be waived.

---

## De-identification

### `E_FORBIDDEN_FILE`

A file inside the submission has a name suggesting a re-identification key or
unredacted PHI (`crosswalk`, `mrn`, `phi`, `linking`).

**Do not just delete it.** The crosswalk needs to exist — it is how a contributor
answers a question about their own data later — it just must not live inside the
submission. Move it somewhere under your institution's PHI controls. The tooling's
default location is outside the submission for exactly this reason:

```bash
python -m openh4d.fetch_dcm2niix --show-cache   # crosswalks live under <cache>/crosswalk/
```

Then work out how it got there, because a copy that reached the submission may
have reached somewhere else too.

---

## Derived products

### `E_UNDECLARED_DERIVED`

A file under `derived/` is not listed in `study.json.derived`. Derived products
need declaring so a downstream user knows what they are and how they were made:

```json
"derived": [
  {"path": "derived/motion_field.nii.gz",
   "description": "Displacement field from t0000 to t0005, computed with <method>"}
]
```

---

## Warnings

### `W_FALLBACK_LAYOUT`

The study uses the single `image4d.nii.gz` form. This is **accepted** — it is not
a failure and does not need fixing. The per-time-point and per-phase forms are
easier to stream, spot-check, and repair one phase of, which is why they are
canonical, but a valid submission can use either.

### `W_CONFORMANCE`

An RFP threshold is not met and a waiver covers it. Recorded for the steering
group; no action needed from the contributor.
