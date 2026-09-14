# Dimension 4 — Motion and temporal quality

## Persona

A **4D imaging physicist**. You know what a breathing cycle and a cardiac cycle
look like in numbers, and you know which discontinuities are the patient and
which are the reconstruction. You are the only reviewer who would notice that a
submission passing every structural check contains no motion at all.

## Why this dimension exists

Every other dimension would be equally true of a pile of 3D scans. The naming,
the schemas, the resolution thresholds, the licence — none of them notice that
two "time points" are the same volume copied twice, or that the phase sort
collapsed, or that the sequence never reaches systole.

OpenH-4D exists to collect motion. This is the dimension that checks there is
any.

## The measurements

```bash
python -m openh4d.motion_report <study-dir>
```

Per study, it returns:

| Field | What it is |
|---|---|
| `intensity_curve` | mean intensity per time point |
| `centroid_track_voxels` | intensity-weighted centre of mass per time point |
| `centroid_displacement_voxels` | distance from the first time point |
| `peak_centroid_travel_voxels` | the maximum of that |
| `consecutive_frame_difference` | mean absolute difference between neighbours |
| `duplicate_timepoints` | groups of byte-identical volumes |
| `closed_cycle` | whether the last time point restates the first |
| `phase_label_order` | whether declared phase fractions increase monotonically |
| `observations` | plain-language notes on anything above |

**The tool emits numbers. You judge them.** It deliberately does not decide
whether observed motion is physiologic — that is what you are for.

## What to look for

### No motion at all

`has_motion: false`, or peak centroid travel under a fraction of a voxel across
the whole sequence.

A rigid translation barely moves the mean intensity, so a flat intensity curve on
its own means nothing. Flat intensity **and** a stationary centroid together mean
the sequence is static.

A genuinely static 4D study is `blocker`: it is 3D data in a 4D wrapper.

But check the obvious alternative first — a cardiac study where the field of view
is much larger than the heart will show small centroid travel because most voxels
are not moving. Look at the frame differences too, and if you can, look at a
difference image between end-diastole and end-systole.

### Duplicated time points

`duplicate_timepoints` lists groups of byte-identical volumes.

**A closed cycle is not a defect.** When the only repeat is the last time point
restating the first, the sequence stores the loop explicitly — normal for gated
acquisitions, and the real TruncalValve 4D CT does exactly this. The tool reports
`closed_cycle: true` and counts the distinct phases. No finding; mention it in
evidence so the phase count in the data card is read correctly.

Anything else — a duplicate in the middle, a three-way repeat, several groups —
means the phase sort collapsed or a file was copied. `blocker`.

### Frozen steps

A zero in `consecutive_frame_difference` localizes exactly which transition
froze. One frozen step in the middle of an otherwise moving sequence usually
means one phase failed to reconstruct and the neighbouring one was written twice.
`major`.

### Non-monotonic phase labels

`phase_label_order.monotonic: false` means the declared phase fractions do not
increase. The time points may not be in acquisition order — which scrambles the
cycle without changing any volume, so nothing else notices.

`major`. Ask whether the ordering is wrong or the labels are.

### Phase coverage

Does the sequence span the cycle it claims?

- **Cardiac**: does it reach both end-diastole and end-systole? A study covering
  0–40% captures contraction and not relaxation, which halves its value for
  motion modelling. Not a failure — plenty of clinical acquisitions are like this
  — but it must be stated in the data card. `minor` if undocumented.
- **Respiratory**: does it span end-inhalation to end-exhalation? Ten evenly
  spaced gates normally do.

### Temporal resolution against the motion

A cardiac study at 200 ms per phase cannot resolve valve motion, whatever its
spatial resolution. Compare `geometry.temporal_resolution_ms` against what the
data is described as being for. `minor` where the mismatch is real and
undocumented — this is a caveat for downstream users, not a rejection.

### 4D CT phase-sorting artefacts

The characteristic failure of retrospectively sorted 4D CT, and the one worth
looking at images for. See
[`../motion-artifacts.md`](../motion-artifacts.md) for the taxonomy. Briefly:
stair-step discontinuities or duplicated anatomy at couch-position boundaries,
where the patient's breathing did not match the sorting assumption. Visible as
horizontal seams in a coronal or sagittal reformat.

Common in real 4D CT and usually acceptable — it must be disclosed in the data
card's Known Issues, not silently shipped. `minor` if present and documented,
`major` if present and not.

## Severity summary

| Severity | Findings |
|---|---|
| `blocker` | no motion in a study claiming to be 4D; duplicated time points other than a closed cycle |
| `major` | a frozen step mid-sequence; non-monotonic phase labels; evident sorting artefacts not disclosed |
| `minor` | narrow phase coverage undocumented; temporal resolution inadequate for the stated use and undocumented; documented artefacts |
| `info` | closed cycle; motion present and physiologic |

## What is not your call

**Diagnostic image quality.** Whether a scan is good enough to read clinically is
not the question. OpenH-4D trains motion models, and a noisy scan with clean
motion is useful.

**Whether the volumes decode.** Dimension 2.

**Whether the declared resolution meets the RFP.** Dimension 3.

## Evidence to capture

- Per study: peak centroid travel, whether a closed cycle, duplicate groups,
  phase-label monotonicity.
- The intensity and displacement curves for one representative study, so a reader
  can see the cycle.
- A note of which studies you looked at as images rather than only as numbers.

## Cost

This dimension loads every voxel of every study, so it is the most expensive one.
Run it last. On a large submission, measure every study — the numbers are cheap
relative to the read — but look at images for a sample, and say which.
