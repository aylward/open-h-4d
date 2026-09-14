# 4D imaging artefacts

A taxonomy for dimension 4. The organising question is not "is this an artefact"
— real clinical data is full of them — but **which kind**:

- **Acquisition-induced**: the patient, the physics, or the scanner. Present in
  the source, expected in clinical data, and something a foundation model should
  probably learn to handle. Disclose in Known Issues; do not reject.
- **Processing-induced**: introduced by sorting, conversion or resampling *after*
  acquisition. These corrupt the motion itself, and they are what dimension 4
  exists to catch.

The distinction is not always clean. When unsure, describe what you see and say
which you suspect, rather than asserting.

---

## Processing-induced — the ones that matter

### Phase-sorting artefacts (4D CT)

**The characteristic failure of retrospectively sorted 4D CT.** The scanner
acquires at one couch position over several breathing cycles, moves, repeats, and
the reconstruction sorts slices into phase bins by a respiratory surrogate. When
the patient's breathing is irregular, slices from different actual breath depths
land in the same bin.

**What it looks like:** stair-step discontinuities at couch-position boundaries in
a coronal or sagittal reformat — the diaphragm or a vessel jumps sideways at a
horizontal seam. Sometimes anatomy duplicated or missing across the seam.

**Where to look:** coronal and sagittal reformats, near the diaphragm, at regular
z intervals matching the couch pitch.

Very common in real 4D CT, and usually acceptable — but it must be in Known
Issues, because a downstream user computing displacement fields across that seam
will get a discontinuity that is not physiology. `minor` if documented, `major`
if not.

### Collapsed phase sort

Time points that are byte-identical, or nearly so, because the grouping heuristic
put everything in one bin and the writer emitted copies.

`motion_report.duplicate_timepoints` finds the exact case. **Except a closed
cycle** — the last time point restating the first is a normal encoding for gated
acquisitions, reported as `closed_cycle: true`, and not a defect.

Anything else is `blocker`.

### Scrambled time order

Time points present and distinct but not in acquisition order. The motion jumps
rather than flows; a displacement curve zigzags where it should be smooth.

`phase_label_order.monotonic: false` catches the case where the declared
fractions disagree with the file order. It cannot catch the case where both are
wrong in the same way — for that, look at the displacement curve and ask whether
it traces a cycle.

Most often from the `InstanceNumber` fallback in the DICOM indexer, which assumes
volume-major write order. `major`.

### Independent resampling

Each phase resampled or registered separately, so they no longer share one voxel
grid. Dimension 2 catches the outright geometry mismatch; the subtler version has
matching shapes and affines but interpolation applied inconsistently, showing as
differing noise texture between phases.

Suspect it when the intensity curve has steps that do not correspond to anatomy.

### Conversion orientation errors

A mirrored or transposed volume from a wrong handedness flip or axis order. See
[`../../openh-4d-convert/references/slicer-seq-nrrd.md`](../../openh-4d-convert/references/slicer-seq-nrrd.md).

**Nothing detects this numerically** — the affine is self-consistent either way.
It takes a person looking at anatomy: is the liver on the right, the heart on the
left, the apex pointing the right way?

Worth a direct look at one volume per submission.

---

## Acquisition-induced — disclose, do not reject

### Respiratory irregularity

The patient did not breathe evenly. Phase bins have unequal true amplitudes even
when sorted correctly. Shows as a displacement curve that is cyclic but not
smooth.

Real physiology. Arguably valuable — a model trained only on metronomic breathing
will not generalise.

### Cardiac arrhythmia

Ectopic beats during a gated cardiac acquisition. One phase looks wrong relative
to its neighbours because it came from a beat with different timing.

Real. Worth disclosing because it affects what the phase labels mean.

### Metal and beam-hardening artefacts

Streaks from pacemakers, valve prostheses, sternal wires, dental work. Especially
common in exactly the cardiac population OpenH-4D targets.

Expected. Disclose in Known Issues if severe enough to obscure the anatomy of
interest.

### Motion blur within a phase

The acquisition window was long relative to the motion, so each phase is
time-averaged rather than instantaneous. Shows as soft edges on fast-moving
structures — valve leaflets, the free wall — while static structures stay sharp.

A temporal resolution limit, not a defect. Dimension 3 compares
`temporal_resolution_ms` against the stated use; document it either way.

### Truncation and partial coverage

The organ extends outside the field of view. `study.json.coverage: "partial"`
declares it, and that is a legitimate declaration — plenty of clinical
acquisitions are partial.

`coverage: "full"` on data that is visibly truncated is a `major` metadata
finding (dimension 3), not an artefact finding.

### Ultrasound-specific

Shadowing, reverberation, dropout at depth, limited sector. Inherent to the
modality. Expected and not a defect. Sector edges moving between frames is
probe motion, which is real.

---

## How to report

**Say which kind you think it is, and why.** "Stair-step discontinuity at
z = 120 in the coronal reformat of `STAN-0003A`, at a couch-position boundary —
consistent with a 4D CT phase-sorting artefact" is actionable. "Image quality
issues" is not.

**Distinguish what you measured from what you saw.** The motion report gives
numbers; artefact identification needs images. Say which studies you looked at
and which you only measured.

**For acquisition-induced artefacts, the finding is about disclosure.** The
artefact itself is not the problem — its absence from Known Issues is. Phrase it
that way, so the contributor understands you are not asking them to re-acquire
the data.
