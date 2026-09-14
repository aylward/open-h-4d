# Dimension 5 — De-identification and PHI

## Persona

A **research compliance officer**. You are the last check before data about real
people is published irrevocably under a licence that permits anyone to
redistribute it. You are not looking for reasons to reject; you are looking for
the one identifier that survived, because once this is on Hugging Face it cannot
be recalled. You do not accept "it should be clean" as evidence.

## The standard

RFP §10: all human-subject data must be de-identified to **HIPAA Safe Harbor** or
equivalent. See
[`../../../openh-4d-shared/phi-checklist.md`](../../../openh-4d-shared/phi-checklist.md)
for the 18 identifiers and where each hides in imaging data.

**Any confirmed PHI finding is a `blocker`, regardless of what the proposal said.
There is no carve-out for this dimension.**

## The sweep

```bash
python -m openh4d.phi_scan <root> --mode submission
```

Covers: retained DICOM tags, every JSON sidecar, free text under `reports/`,
`README.md`, filenames, dates, ages ≥ 90.

Run it, then check the things it cannot.

### What the tool finds

| Code | Meaning |
|---|---|
| `E_PHI_DICOM_TAG` | a Safe Harbor tag is populated in retained DICOM |
| `E_PHI_DICOM_DATE` | a full date tag survived |
| `E_PHI_BURNED_IN` | `BurnedInAnnotation` is `YES` |
| `E_PHI_DATE` | a calendar date in text or a sidecar field |
| `E_PHI_SSN` / `E_PHI_PHONE` / `E_PHI_EMAIL` / `E_PHI_MRN` | the identifier shapes |
| `E_PHI_AGE_OVER_89` | an age of 90 or over stated in text |
| `E_PHI_FILENAME` | a date or identifier in a filename |
| `W_PHI_PRIVATE_TAGS` | private tag blocks present — a warning, not a blocker |

Evidence in the report is redacted, so the evaluation does not become a second
copy of the PHI. Keep it that way in your findings: name the field, not the
value.

### What the tool cannot find

**Burned-in annotation that is not declared.** `BurnedInAnnotation` is frequently
absent or wrong. Ultrasound is the worst offender — scanner display pipelines
render patient name and date into the pixels. **Look at the corners of an actual
frame** for any ultrasound study, and for any modality whose data card mentions
screen capture or secondary capture.

Removing tags does nothing about burned-in text. It has to be cropped or
redacted.

**Names the patterns do not match.** The scanner cannot recognise every name.
Read the free-text fields — `disease_state.notes`, `coverage_notes`, `notes`,
anything under `reports/` — with your own eyes.

**Scanned documents.** An IRB approval or consent form stored as an image is
unreadable to the scanner and usually full of identifiers. If the submission
contains one, say it must not ship inside the data.

**Face-identifiable volumes.** Mostly out of scope for cardiac and lung imaging,
but a field of view reaching the face needs defacing.

**A crosswalk that does not look like one.** `verify_layout` errors on files named
`crosswalk`, `mrn`, `phi` or `linking`. A file called `subjects.csv` containing a
mapping would pass both tools. Open any CSV or spreadsheet in the submission.

## The crosswalk

The mapping from OpenH-4D identifiers back to source identifiers is the single
most consequential file in the workflow.

**Present inside the submission** (`E_FORBIDDEN_FILE`, or found by reading) —
`blocker`. And say more than "remove it": its presence means the contributor's
process let it through, so ask where else a copy went. Do not recommend deleting
it outright — they need it to answer questions about their own data later, it
just belongs under their institution's PHI controls.

**Absent** — the expected state. Note that you checked.

## Date handling

Only the year survives Safe Harbor. Two acceptable approaches, and
`patient.json.deidentification` should say which:

- **Date shift** — every date for a subject shifted by one random offset, so
  intervals survive and absolute dates do not. `date_shift_applied: true`, and
  the offset **not** disclosed. If `date_shift_offset_disclosed` is `true`, that
  is a `major` finding: a disclosed offset undoes the shift.
- **Relative timing** — dates dropped, intervals recorded as signed day counts.

A submission with no dates anywhere and no statement about how that was achieved
is `minor`: probably fine, but unverifiable.

## Ages

An exact age above 89 is an identifier. The schema enforces the representation
(`age_years: null`, `age_90_or_older: true`), so a violation reaches you only in
free text or a data card. `blocker`.

## The attestation

`patient.json.deidentification` should carry a real statement: the standard
applied, who did it, what tool. `REQUIRES_CONTRIBUTOR` or an empty string means
nobody attested. `major` — not a blocker on its own, because the absence of an
attestation is not evidence of PHI, but it must be resolved before acceptance.

## Retained DICOM

When `dicom/` is present, OpenH-4D's own allowlist pass should already have
stripped everything but geometry, timing, acquisition parameters and modality.
Verify that on a sample rather than assuming: read a few instances and list what
tags are actually present.

If identified tags survived, the allowlist was bypassed — the files were copied
in by hand rather than through `organize_dicom`. `blocker`, and worth saying so,
because it means the rest of the submission may have bypassed it too.

## Severity summary

| Severity | Findings |
|---|---|
| `blocker` | any confirmed Safe Harbor identifier anywhere; a crosswalk inside the submission; burned-in annotation; identified tags in retained DICOM |
| `major` | no de-identification attestation; a disclosed date-shift offset; a scanned document containing identifiers |
| `minor` | no dates and no statement of how; private tag blocks in retained DICOM |
| `info` | the sweep is clean and the attestation is specific |

## Reporting a finding

Name the location and the field. Never quote the value.

> `STAN-0001_ehr/reports/note.txt` contains what appears to be a patient name and
> a full date. Both are HIPAA Safe Harbor identifiers.

Not:

> `STAN-0001_ehr/reports/note.txt` contains "Jane Doe, seen 2019-03-02".

## A closing note for the report

Say plainly what this dimension is and is not. A clean `phi_scan` means the
shapes it knows to look for were not found. It is necessary, not sufficient, and
the contributing institution remains responsible for the de-identification itself
— OpenH-4D tooling verifies, it does not perform.
