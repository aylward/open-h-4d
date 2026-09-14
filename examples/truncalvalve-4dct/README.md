# Truncal Valve 4D CT example

Converts a 3D Slicer 4D sequence (`.seq.nrrd`) into the OpenH-4D submission
layout. One subject, one cardiac-gated 4D CT study, 21 time points.

```bash
uv run python examples/truncalvalve-4dct/build_example.py --out /tmp/oh4d-tv
```

Downloads ~1.3 GB on first run and caches it.

## Why this example exists

Two properties of a Slicer sequence are silent failures if handled wrongly. Both
are handled in `openh4d.convert_nrrd`; this example is the end-to-end check that
they stay handled.

**The time axis comes first.** The real header reads:

```
sizes: 21 512 391 218
kinds: list domain domain domain
space directions: none (0.5996,0,0) (0,0.5996,0) (0,0,0.7)
```

That is 21 time points of a 512x391x218 volume, time leading. SimpleITK surfaces
that leading `list` axis as *vector components* on a 3D image — `GetDimension()`
returns 3, not 4 — so the frames arrive as the last axis of the array. Transpose
it wrong and you get 218 "time points" of a 21-slice volume, which still opens.

**The handedness flips.** The file declares `space: right-anterior-superior`.
SimpleITK normalizes every image to LPS on read. NIfTI is RAS. So the affine has
to be flipped back, and getting that wrong mirrors the anatomy without raising
anything at all: the volume opens, the spacing is right, and left and right are
swapped.

## The check no test replaces

Open `t0000.nii.gz` alongside the original `.seq.nrrd` in 3D Slicer or ITK-SNAP
and confirm they overlay. The unit tests check the affine against the geometry
the header declares, which catches an arithmetic error — but only a human
looking at anatomy catches a conceptual one.

## What it produces

```
/tmp/oh4d-tv/
├── README.md              the data card, with the measured motion in it
├── LICENSE                CC BY 4.0
├── manifest.json
├── CHOP-0001A/
│   ├── study.json
│   └── t0000.nii.gz ... t0020.nii.gz
└── CHOP-0001_ehr/patient.json
```

## Honest gaps

Phase fractions are evenly spaced across the cycle, because the source records
frame indices rather than measured R-R percentages. `study.json` says so in its
notes rather than presenting the even spacing as if it were measured.

CT dose is not published upstream, so it is recorded as an explicit waiver with a
justification rather than guessed.

## Options

| Flag | Effect |
|---|---|
| `--source PATH` | use a local file instead of downloading |
| `--cache DIR` | override the download cache location |
