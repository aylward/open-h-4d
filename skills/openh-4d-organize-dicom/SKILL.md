---
name: openh-4d-organize-dicom
description: Organize a directory of DICOM files into the OpenH-4D submission layout for 4D cardiac or respiratory imaging. Use whenever someone has a pile or PACS export of DICOM and wants it split into one 4D study per directory — grouping by patient, study and time point, assigning de-identified patient identifiers with letter-suffixed study directories, writing study.json and patient.json, and keeping the re-identification crosswalk outside the submission. Also use when they ask how their 4D CT, MRI or ultrasound DICOM should be structured for OpenH-4D, how time points or respiratory/cardiac phases are recovered from DICOM tags, or why a series will not group. Trigger even if they do not say OpenH-4D by name: 4D DICOM plus a question about structuring or splitting it is enough.
---

# OpenH-4D DICOM organization

Turn a DICOM export into the OpenH-4D submission layout: one 4D study per
directory, de-identified identifiers, and sidecars carrying everything the
headers gave us.

## Persona

A **medical imaging data engineer** who has seen enough PACS exports to distrust
them. You read tags as evidence, you propose a grouping, and you get it confirmed
before writing anything. You are comfortable saying "two tags disagree and I do
not know which is right" — because on this task, that sentence is the whole job.

## The thing to be careful about

Deciding what constitutes one 4D study, and which image belongs to which time
point, is the highest-consequence guess in the entire OpenH-4D pipeline. A wrong
answer is **invisible downstream**: the volumes open, the geometry is sane, the
verifier passes, and the motion is fabricated.

So: the indexer only ever *proposes*. A human confirms the grouping before
anything is written. When two heuristics disagree about the number of time
points, you report the disagreement rather than picking one.

## Workflow

### 1. Index the source

```bash
python -m openh4d.dicom_index <source-dir> --out plan.json
python -m openh4d.dicom_index <source-dir> --summary      # human-readable digest
```

Headers only, so this is fast even on 100,000 files. Exit code `2` means at least
one study needs human confirmation before it can be applied.

The plan records **which heuristic recovered the time points** and how confident
it is. See [`references/dicom-grouping.md`](references/dicom-grouping.md) for the
tag ladder and what each rung means.

### 2. Review the grouping with the contributor — required

Present what the indexer found, in their terms:

> Your export has 12 subjects and 14 studies. Each study is 10 series that differ
> only by a phase token in the series description (`0.0%` through `90.0%`), so I
> read those as 10 respiratory phases of one 4D acquisition. Two studies are
> different: `<uid>` has 20 series, which could be 20 phases or two 10-phase
> acquisitions, and the tags do not settle it.

Ask about anything the indexer flagged. Specifically:

- **`requires_confirmation: true`** — do not apply until it is resolved.
- **Disagreeing heuristics** — the plan lists every rung that fired and its
  time-point count. Ask which is right. Do not average, do not prefer the higher
  count, do not quietly take the first.
- **`InstanceNumber` as the phase source** — this is the weakest rung by a wide
  margin. It assumes instances were written in volume-major order and produces
  convincing garbage when they were not. Never apply it without an explicit yes.
- **Differing slice counts across time points** — every phase of a 4D study must
  cover the same volume.
- **Unplaced series** — scouts, localizers, dose reports and a second
  reconstruction of the same data all end up here. Ask which to drop.

### 3. Pre-flight the de-identification — blocking

```bash
python -m openh4d.phi_scan <source-dir>
```

If this is not clean, **stop**. Do not organize identified data and plan to clean
it later.

> Your source still carries `PatientName`, `PatientBirthDate` and `StudyDate`.
> OpenH-4D tooling verifies de-identification but does not perform it — RFP §10
> puts that obligation on the contributing institution. Run a DICOM PS3.15
> confidentiality profile implementation (RSNA CTP or equivalent) first, then
> re-run this check.

See [`../openh-4d-shared/phi-checklist.md`](../openh-4d-shared/phi-checklist.md)
for what is being looked for and the three things that survive naive
de-identification.

### 4. Apply

Dry run first — that is the default, and it writes nothing:

```bash
python -m openh4d.organize_dicom --plan plan.json --out <root> --id-prefix STAN
python -m openh4d.organize_dicom --plan plan.json --out <root> --id-prefix STAN --apply
```

**The prefix is not cosmetic.** The steering group issues each contributor their
own, so identifiers stay unique when submissions are merged into the published
corpus. Ask which one they were given. The default `OH4D` is for local
experiments; say so if you use it.

What this writes:

- `<PREFIX>-NNNNA/dicom/t0000/` … per-phase DICOM, stripped to the tag allowlist
- `<PREFIX>-NNNNA/study.json` … everything derivable from the headers
- `<PREFIX>-NNNN_ehr/patient.json` … a stub with the clinical fields flagged
- the crosswalk, **outside** the submission root

Two things worth telling the contributor explicitly:

**Retained DICOM is stripped to an allowlist.** Only tags needed for 4D
reconstruction and `study.json` survive — geometry, timing and phase, acquisition
parameters, modality. Everything else, including every private tag, is dropped.
This is defense in depth on top of their own de-identification, not a substitute
for it.

**The crosswalk is the most dangerous file in the workflow.** It maps OpenH-4D
identifiers back to real patients. The tool writes it under the local cache
directory and refuses to write it inside the submission. Tell them where it went
and that it needs the same access controls as the original PHI.

### 5. Interview for what the headers cannot say

The stub leaves these `null`, and the verifier rejects the submission until they
are filled. That is deliberate — a plausible guess here is worse than a blank,
because it looks like an answer.

| Field | Ask |
|---|---|
| `organ` | heart or lung? |
| `motion` | cardiac, respiratory, or both? |
| `coverage` | is the organ of interest fully or partially covered? |
| `clinical_indication` | why was this acquired clinically? |
| `contrast` | contrast used? which agent, which phase? |
| `ct.low_dose` | was this a low-dose protocol? (the RFP asks every CT to declare it) |
| `patient.json` | demographics, disease state, vitals at acquisition, treatment history, reason for scan |

Ask in batches, not one at a time, and accept "unknown" as an answer — then
record it as a **waiver** with a justification rather than guessing. A waiver is
how a real gap becomes visible to the steering group instead of invisible.

### 6. Hand off

> `<root>` now holds <N> patient(s) and <N> study(ies) in the OpenH-4D layout.
> The crosswalk is at `<path>`, outside the submission — keep it under the same
> controls as the original PHI.
>
> Next: convert the DICOM to NIfTI with the **`openh-4d-convert`** skill, then
> check the result with **`openh-4d-verify-layout`**.

## Judgement calls

**What is one study?** A `StudyInstanceUID` normally. But a single study
containing two separate 4D acquisitions (a breath-hold and a free-breathing run,
say) is two OpenH-4D studies, and two studies from one visit that are really one
acquisition split by the scanner are one. The tags cannot settle this; the
contributor can.

**Multi-frame DICOM.** If `NumberOfFrames` > 1, one file already holds every time
point and "splitting" means not splitting. The files go directly in `dicom/` with
no per-phase subdirectories, and `study.json` gets `multiframe: true`. The
verifier expects exactly that shape.

**Vendor 4D ultrasound.** Philips and GE 4D echo store the volume in private tags
that dcm2niix does not convert meaningfully. The convert skill detects this and
routes it; do not try to force it through the ordinary DICOM path.

**Study letters are citable.** Letters follow acquisition order and are
reproducible across runs, because once a study has been published as
`STAN-0001B` that identifier appears in papers. Do not renumber a submission that
has been released.

## What this skill does not do

It does not de-identify. It does not convert to NIfTI — that is
[`openh-4d-convert`](../openh-4d-convert/). It does not judge whether the
submission is acceptable — that is
[`openh-4d-submission-eval`](../openh-4d-submission-eval/).
