# OpenH-4D submission layout specification

Normative. Every OpenH-4D skill reads this file, and `openh4d.verify_layout` enforces it.
Where this document and the code disagree, that is a bug — `tests/test_spec_consistency.py`
asserts the naming grammar below is character-for-character identical to the patterns compiled
in `openh4d.naming`.

A submission is a directory tree holding **one 4D study per directory**, plus one auxiliary
directory per patient. Images are `.nii.gz` or DICOM.

---

## 1. Naming grammar

```
patient_id ::= ^[A-Z][A-Z0-9-]{1,46}[0-9]$
study_dir  ::= ^([A-Z][A-Z0-9-]{1,46}[0-9])([A-Z]+)$
ehr_dir    ::= ^([A-Z][A-Z0-9-]{1,46}[0-9])_ehr$
```

Three constraints, each load-bearing:

**A patient identifier must end in a digit.** Without this, `STAN-001AB` parses two ways
(`STAN-001` + `AB`, or `STAN-001A` + `B`). With it, the split point is provably "after the last
digit", so there is exactly one parse and no separator character is needed.

**Underscore is banned inside a patient identifier.** That is what makes `_ehr` unambiguous.

**Uppercase is mandatory.** Windows and macOS filesystems are case-insensitive, so
`STAN-0001a` and `STAN-0001A` would collide there but not on Linux. The verifier also errors if
two directory names differ only in case, so a submission prepared on Linux cannot break on
Windows.

### Study letter suffix

The suffix always starts at `A`, **even when a patient contributed only one study**. One rule,
no special case, and adding a second study later never renames the first.

Past 26 studies the suffix continues in bijective base-26, the scheme spreadsheet columns use:
`A … Z, AA, AB … ZZ, AAA`. A patient's suffixes must form the contiguous sequence starting at
`A`; a gap (`…A`, `…C` with no `…B`) is an error, because it almost always means a directory
was lost in transfer rather than deliberately skipped.

### Patient identifier prefix

The steering group issues each contributor a prefix (`STAN`, `DUKE`, …) so identifiers stay
unique when submissions are merged into the published corpus. Canonical form is
`<PREFIX>-NNNN` with at least four zero-padded digits: `STAN-0001A`, `STAN-0001_ehr`.

The default prefix `OH4D` that the tooling falls back to is for local experiments only. Use
your issued prefix for anything you intend to submit.

The submission must **never** contain the original `PatientID`, medical record number, or any
other identifier that links back to a person. See §7.

---

## 2. Submission root

```
<submission-root>/
├── README.md                   REQUIRED  the data card (HF frontmatter + body)
├── LICENSE                     REQUIRED  CC BY 4.0 full text (the data license)
├── manifest.json               REQUIRED  generated index (§5)
├── checksums.sha256            OPTIONAL  generated on request
├── STAN-0001A/                 one 4D study
├── STAN-0001B/                 a second study from the same patient
├── STAN-0001_ehr/              auxiliary information for patient STAN-0001
└── STAN-0002A/ …
```

`README.md` and `LICENSE` live **only** at the submission root. There are no per-study data
cards. Hugging Face renders `README.md` as the dataset landing page, so it is the one
user-facing document.

Anything else at the root is a warning. Anything anywhere in the tree whose name matches
`*crosswalk*`, `*mrn*`, `*phi*`, or `*linking*` (case-insensitive) is an **error** — see §7.

---

## 3. Study directory

Every study directory holds a `study.json` (§6) and the image data in exactly one of three
forms.

### Form A — per-time-point volumes (canonical)

```
STAN-0001A/
├── study.json
├── t0000.nii.gz
├── t0001.nii.gz
└── …
```

### Form B — DICOM with per-phase subdirectories (canonical)

```
STAN-0001A/
├── study.json
└── dicom/
    ├── t0000/ *.dcm
    ├── t0001/ *.dcm
    └── …
```

For **enhanced / multi-frame DICOM**, splitting into per-phase subdirectories is structurally
impossible: one file already contains every time point. Place the container file(s) directly in
`dicom/` with no `t####/` subdirectories and set `study.json.multiframe` to `true`.

### Form C — a single 4D NIfTI (accepted fallback)

```
STAN-0001A/
├── study.json
└── image4d.nii.gz
```

The filename is fixed. This form is accepted but produces a `W_FALLBACK_LAYOUT` warning:
per-time-point files are easier to stream, to spot-check, and to repair when one phase is bad.

### Rules

- Exactly one of the three forms per study. Mixing Form A and Form C is an error.
- **Form B plus Form A together is legal** when `file_layout` is `dicom_phases` and
  `derived_from_dicom` is `true`. That is the normal result of running `organize-dicom` and then
  `convert`, and it must not be flagged.
- Time-point indices run contiguously from `0000`, and the count must equal
  `study.json.n_timepoints`.
- `n_timepoints` must be at least 2. This is a 4D initiative; a single time point is a 3D study.

### Optional subdirectories

```
STAN-0001A/
├── seg/           labels.json, plus t0000_<set>.nii.gz per time point
│                  or seg4d_<set>.nii.gz for a single 4D mask
├── landmarks/     t0000.json … each {coordinate_system, units, points[]}
├── derived/       motion fields, meshes — every entry declared in study.json.derived
└── provenance.json   auto-written by the tooling: versions, arguments, timestamps
```

`seg/labels.json` maps each integer label to `{name, anatomy, coding?}`. Segmentation files
must share the geometry of the images they annotate.

---

## 4. EHR directory

```
STAN-0001_ehr/
├── patient.json    REQUIRED  (§6)
├── waveforms/      OPTIONAL  ecg_*.csv, resp_*.csv — time-relative, de-identified
├── reports/        OPTIONAL  de-identified text or markdown
└── attachments/    OPTIONAL  anything else, described in patient.json.extra
```

One per patient, at patient level — no letter suffix. Waveform timestamps are relative to the
start of the acquisition, never wall-clock.

---

## 5. `manifest.json`

A generated index at the submission root: counts, per-patient and per-study summaries,
modality and organ breakdowns, and SHA-256 hashes of the JSON sidecars.

Hashes cover the **sidecars only**. Metadata is what silently drifts; image bytes are covered by
the optional `checksums.sha256` when transfer integrity is in question. Hashing multiple
gigabytes on every verification run would make the verifier unusable.

Regenerate with `python -m openh4d.manifest <submission-root>`. Schema:
`openh4d/schemas/manifest.schema.json`.

---

## 6. Sidecar metadata

| File | Scope | Schema | Annotated example |
|---|---|---|---|
| `<patient>_ehr/patient.json` | one per patient | `openh4d/schemas/patient.schema.json` | [`patient-json-template.json`](patient-json-template.json) |
| `<study>/study.json` | one per study | `openh4d/schemas/study.schema.json` | [`study-json-template.json`](study-json-template.json) |

Both are keyed to OpenH-4D RFP §8. Copy the template and replace every value; leave
inapplicable fields `null` rather than deleting them, so a reviewer can tell "not applicable"
from "forgot".

### RFP conformance rules

Checked by code against `study.json`. Each is an **error**, unless `study.json.waivers[]`
carries a matching entry with a justification, in which case it becomes a **warning**.

| rule id | condition |
|---|---|
| `ct_in_plane_resolution` | CT → in-plane resolution ≤ 1.0 mm |
| `ct_slice_spacing_cardiac` | CT of the heart → slice spacing ≤ 2.0 mm |
| `ct_slice_spacing_lung` | CT of the lung → slice spacing ≤ 3.0 mm |
| `ct_low_dose_declared` | CT → `ct.low_dose` is stated (not null) |
| `mr_cine_resolution_declared` | MR → in-plane resolution and temporal resolution both stated |
| `us_source_declared` | US → `us.source` is one of `4d_tee`, `4d_percutaneous`, `other` |
| `min_timepoints` | `n_timepoints` ≥ 2 — **never waivable** |

### Header cross-checks

Also code, also errors:

- Declared voxel spacing matches the NIfTI header of the first time point within 0.001 mm.
- Every time point shares an identical shape and affine.
- `n_timepoints` equals the number of image files actually present.

---

## 7. De-identification

All human-subject data must be de-identified to **HIPAA Safe Harbor** or equivalent (RFP §10).
The obligation is the contributor's.

**OpenH-4D tooling verifies de-identification; it does not perform it.** Use a mature tool
(RSNA CTP, a DICOM PS3.15 Basic Application Level Confidentiality Profile implementation)
before running anything here. The tooling adds two layers on top:

- `organize-dicom` refuses to run on a source that still carries obvious PHI.
- When DICOM is retained, only tags on a conservative allowlist survive the copy — geometry,
  timing and phase, acquisition parameters, modality. This is defense in depth, not a
  substitute for de-identifying properly.

### The crosswalk

The mapping from OpenH-4D identifiers back to source `PatientID`s is written **outside the
submission root**, under the tool's cache directory. Three independent guards enforce this: the
tool refuses to write it inside the root; `.gitignore` excludes `*crosswalk*`; and the verifier
errors on any matching file found under the root.

Keep the crosswalk under the same controls as the original PHI. Do not ship it.

---

## 8. Checking your submission

```
python -m openh4d.verify_layout <submission-root>
```

Prints a JSON report and exits 0 when compliant, 1 when not. Errors block acceptance; warnings
do not, but they are worth reading. `references/fixing-common-failures.md` in the
`openh-4d-verify-layout` skill maps each error code to its fix.
