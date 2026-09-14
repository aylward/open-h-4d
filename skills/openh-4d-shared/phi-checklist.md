# HIPAA Safe Harbor identifiers, and where they hide in imaging data

RFP §10: all human subject data must be de-identified to **HIPAA Safe Harbor**
standards or equivalent. This is the canonical list, from 45 CFR §164.514(b)(2),
with the places each one actually turns up in a 4D imaging submission.

**OpenH-4D tooling verifies de-identification; it does not perform it.** The
obligation is the contributor's, and mature implementations exist (RSNA CTP, any
DICOM PS3.15 Basic Application Level Confidentiality Profile implementation). Use
one of those, then run `python -m openh4d.phi_scan` to check.

## The 18 identifiers

| # | Identifier | Where it shows up |
|---|---|---|
| 1 | Names | `PatientName`, `ReferringPhysicianName`, `PerformingPhysicianName`, `OperatorsName`, free text in reports, filenames |
| 2 | Geographic subdivisions smaller than a state | `PatientAddress`, `InstitutionAddress`, sometimes `StudyDescription` |
| 3 | All date elements except year | `StudyDate`, `SeriesDate`, `AcquisitionDate`, `ContentDate`, `PatientBirthDate`, dates in free text and filenames |
| 4 | Telephone numbers | `PatientTelephoneNumbers`, report text |
| 5 | Fax numbers | report letterheads |
| 6 | Email addresses | contact fields, report text |
| 7 | Social security numbers | `OtherPatientIDs`, report text |
| 8 | Medical record numbers | `PatientID`, `OtherPatientIDs`, `MedicalRecordLocator`, filenames |
| 9 | Health plan beneficiary numbers | `InsurancePlanIdentification` |
| 10 | Account numbers | `AccessionNumber` |
| 11 | Certificate/license numbers | rare; report text |
| 12 | Vehicle identifiers | rare; report text |
| 13 | Device identifiers and serial numbers | `DeviceSerialNumber` |
| 14 | URLs | report text, `extra` fields |
| 15 | IP addresses | rare; private tags |
| 16 | Biometric identifiers | face-identifiable volumes (see below) |
| 17 | Full-face photographs and comparable images | **burned-in annotation**, and head/neck volumes |
| 18 | Any other unique identifying number or characteristic | private tags, `StudyID`, local accession schemes |

## Ages of 90 and over

An exact age above 89 is itself an identifier, because it narrows the population
sharply. Collapse every such age into a single category. In `patient.json`:

```json
"demographics": {"age_years": null, "age_90_or_older": true}
```

The schema enforces this: `age_years` is capped at 89, and setting
`age_90_or_older` while also giving an age is an error.

## Dates

Only the year survives Safe Harbor. Two workable approaches:

- **Date shift.** Shift every date for a subject by the same random offset, so
  intervals are preserved and absolute dates are not. Record that you did this in
  `patient.json.deidentification.date_shift_applied`, and do **not** publish the
  offset.
- **Relative timing.** Drop dates entirely and record intervals, which is what the
  schema is built for: `treatment_history[].days_relative_to_scan` is a signed
  integer, negative before the scan.

`phi_scan` flags any `YYYY-MM-DD` or `YYYY/MM/DD` pattern outside the
`deidentification` block, and any full DICOM date tag.

## Three things that survive naive de-identification

**Burned-in annotation.** Patient name and date rendered into the pixel data
itself. Removing tags does nothing. `BurnedInAnnotation` (0028,0301) should say
`YES` when present, but it is often wrong, so ultrasound in particular needs a
look at the actual corners of a frame. Crop or redact the region.

**Private tags.** Vendors store arbitrary data in private blocks, including
identifiers, and there is no general way to know what is in one. OpenH-4D drops
every private tag when it retains DICOM. `phi_scan` reports their presence as a
warning so you know what was dropped.

**Filenames and directory names.** They travel with the data and are not covered
by any DICOM tag sweep. `scan_1962-04-17.nii.gz` leaks a date as surely as
`ContentDate` does.

## Re-identification keys

The mapping from OpenH-4D identifiers back to source patient identifiers is the
most dangerous file in the whole workflow. `organize_dicom` writes it outside the
submission root by default and refuses to write it inside; the verifier errors on
any file under a submission whose name matches `*crosswalk*`, `*mrn*`, `*phi*` or
`*linking*`.

Keep the crosswalk under the same access controls as the original PHI. Do not
ship it, do not commit it, do not put it in the submission's parent directory
where a `zip -r` would catch it.

## Face-identifiable volumes

A head or neck CT/MR at sub-millimetre resolution can be surface-rendered into a
recognisable face — identifier 16 and 17 territory. OpenH-4D collects cardiac and
lung imaging, so this is mostly out of scope, but a field of view that reaches
the face needs defacing before release.

## Checking

```bash
# Before organizing: is the source de-identified at all?
python -m openh4d.phi_scan <source-dir>

# After building: audit the finished submission
python -m openh4d.phi_scan <submission-root> --mode submission
```

Exit 0 means clean, 1 means findings. Evidence in the report is redacted, so the
report itself does not become a second copy of the PHI.

A `phi_scan` pass is necessary, not sufficient. It finds the shapes it knows how
to look for. It cannot read a scanned consent form stored as pixels, recognise a
name it has no pattern for, or tell you whether your IRB approved public release.
