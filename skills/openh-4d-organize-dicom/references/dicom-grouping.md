# Recovering 4D structure from DICOM

How `openh4d.dicom_index` decides what a study is and which image belongs to
which time point, what each heuristic is worth, and what to do when they
disagree.

## Grouping levels

| Level | Primary key | Fallback |
|---|---|---|
| Patient | `PatientID` (0010,0020) | stable hash of `PatientName` + `PatientBirthDate`; both absent → quarantined as `UNKNOWN-NO-IDENTIFIER` |
| Study | `StudyInstanceUID` (0020,000D) | `StudyDate` + `StudyTime` + `AccessionNumber` |
| Series | `SeriesInstanceUID` (0020,000E) | — |
| Time point | the ladder below | human confirmation |

Study letters are assigned by sorting a patient's studies on
`(StudyDate, StudyTime, StudyInstanceUID)`. That ordering is deterministic, so
re-running on the same input always produces the same letters — which matters
because a published study letter is part of a citable identifier.

## Two topologies

**One series per time point.** Each respiratory or cardiac phase was reconstructed
as its own series, and the phase is written into `SeriesDescription`. This is the
shape most 4D CT exports arrive in. Series join one 4D set when they share
`StudyInstanceUID`, `ImageOrientationPatient`, `PixelSpacing`, `Rows`/`Columns`
and slice count, and their descriptions differ only by a phase token.

**One series holding the whole 4D volume.** Time points are separated inside the
series by one of the temporal tags below.

Both are handled. The indexer reports which it found.

## The tag ladder

Tried in order. The first that fires is proposed; **every other rung that also
fires is recorded**, so a disagreement about the time-point count is visible
rather than silently resolved.

### 1. `TemporalPositionIdentifier` (0020,0100)

With `NumberOfTemporalPositions` (0020,0105). The cleanest signal there is: it
exists to say exactly this. High confidence when the declared count matches the
number of distinct values found.

### 2. Phase token in `SeriesDescription` (0008,103E)

A percentage in the description — `4D CT Phase 40.0%`, `Resp 90%` — or an
inhale/exhale word. High confidence, because a description containing a phase
percentage is not ambiguous about what it means.

This is what the TCIA 4D-Lung collection uses, and what most treatment-planning
4D CT exports use.

The fraction and the **verbatim label** are both kept. The label is the only
record of what the source actually said, and `study.json` carries it through to
the published data.

### 3. `NominalPercentageOfCardiacPhase` (0020,9241)

From enhanced cardiac DICOM. High confidence where present.

### 4. `TriggerTime` (0018,1060)

With `CardiacNumberOfImages` (0018,1090). Distinct trigger times are distinct
cardiac phases. Medium confidence: reliable for cardiac-gated CT and MR, but
trigger times can repeat across a multi-cycle acquisition.

### 5. `AcquisitionNumber` (0020,0012)

Medium confidence. Often correct, but vendors use it inconsistently — sometimes
it counts acquisitions, sometimes reconstructions.

### 6. `ContentTime` / `AcquisitionTime` (0008,0033 / 0008,0032)

Clustering by timestamp. **Low confidence**, and worth understanding why: helical
4D CT acquires slices continuously, so timestamps form a gradient rather than
clusters, and any clustering you find may be an artefact of the binning rather
than a property of the acquisition.

### 7. `InstanceNumber` modulo slices-per-volume

**Low confidence, and never applied without explicit human confirmation.**

This rung assumes instances were written in volume-major order — all of phase 0,
then all of phase 1. When that assumption holds it is correct. When it does not,
it produces a result that looks entirely plausible and is entirely wrong: the
right number of time points, the right number of slices, and anatomy scrambled
across phases.

There is no way to detect the failure from the data. That is why it is gated on a
human saying yes.

## When rungs disagree

The plan records every rung that fired and its time-point count:

```json
"phase_source": "SeriesDescription",
"n_timepoints": 10,
"alternatives": [{"source": "AcquisitionNumber", "n_timepoints": 2}],
"problems": ["heuristics disagree on the time-point count: SeriesDescription says 10, but {'AcquisitionNumber': 2}. This is not resolved automatically -- confirm which is right before applying the plan."],
"confidence": "low",
"requires_confirmation": true
```

**Do not resolve this yourself.** Both answers are internally consistent; only
someone who knows how the data was acquired can say which describes it. Ask, and
put the alternatives in front of them.

A common real case: `SeriesDescription` says 10 phases and `AcquisitionNumber`
says 2, because the scanner recorded two acquisitions that were each
reconstructed into 10 phases. Which is a 4D study — one of 10 phases, or two of
5? Only the contributor knows.

## Slice ordering within a time point

Slices are sorted by their position along the slice normal:

```
n = cross(ImageOrientationPatient[0:3], ImageOrientationPatient[3:6])
key = dot(ImagePositionPatient, n)
```

**Not `SliceLocation`**, whose sign is unreliable across vendors. **Not
`InstanceNumber`**, which reflects transfer order rather than geometry.

Three things get checked once sorted:

- **Duplicate positions within one time point** → error. Two slices at the same
  physical location usually means two phases were merged into one.
- **Non-uniform spacing** → warning. dcm2niix will also complain. Real, but
  sometimes acceptable; the contributor decides.
- **Differing slice counts across time points** → error. Every phase of a 4D
  study must cover the same volume.

## Enhanced / multi-frame DICOM

`NumberOfFrames` (0028,0008) > 1 means one file is a container holding every time
point. "Splitting" then means *not* splitting:

- Files go directly in `<study>/dicom/`, no `t####/` subdirectories.
- `study.json` gets `file_layout: "dicom_phases"` and `multiframe: true`.
- Time points live in `PerFrameFunctionalGroupsSequence` →
  `FrameContentSequence.TemporalPositionIndex` / `DimensionIndexValues`; slice
  position in `PlanePositionSequence.ImagePositionPatient`.
- dcm2niix understands Enhanced CT and MR and emits a 4D NIfTI, which
  `convert_dicom` then splits into per-time-point files.

The verifier expects exactly this shape and will reject per-phase subdirectories
alongside `multiframe: true`.

## Vendor 4D ultrasound

Philips and GE store 4D echo in private tag blocks that dcm2niix does not convert
meaningfully. Handing such a file to the ordinary path produces a NIfTI that opens
cleanly with the wrong geometry — worse than a failure.

`openh4d.us_vendor` detects these from the private creator string and routes them.
See [`../../openh-4d-convert/references/us-vendor.md`](../../openh-4d-convert/references/us-vendor.md).

## Files that are not DICOM

Skipped, not fatal. A contributor export routinely contains `DICOMDIR`,
thumbnails, viewer executables and stray notes. The plan records how many were
skipped and lists the first fifty, which is usually enough to notice if something
that should have been DICOM was not.

## Identifiers

The submission never contains the source `PatientID`. Patients are numbered
`<PREFIX>-NNNN` in sorted source-identifier order, using the prefix the steering
group issued to the contributor.

The crosswalk — the mapping back to source identifiers — is written outside the
submission root by default, under the tool's cache directory. The tool refuses to
write it inside, `.gitignore` excludes it, and the verifier errors on any file
under a submission whose name looks like one. Three independent guards, because
this is the file whose leak would matter most.
