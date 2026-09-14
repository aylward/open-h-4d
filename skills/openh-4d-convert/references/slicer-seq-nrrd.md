# 3D Slicer sequences and other NRRD sources

What `openh4d.convert_nrrd` handles, and the two properties of a Slicer sequence
that are silent failures if handled wrongly.

Everything below was established by reading the header of the real
`TruncalValve_4DCT.seq.nrrd` — 21 cardiac phases, 1.3 GB — not from
documentation.

## A real header

```
NRRD0005
type: short
dimension: 4
space: right-anterior-superior
sizes: 21 512 391 218
space directions: none (0.599609375,0,0) (0,0.599609375,0) (0,0,0.69999999999999984)
kinds: list domain domain domain
labels: "frame" "" "" ""
endian: little
encoding: gzip
space origin: (-145.19999694824219,8.9416399002075195,903.09997558593739)
measurement frame: (1,0,0) (0,1,0) (0,0,1)
DataNodeClassName:=vtkMRMLScalarVolumeNode
axis 0 index type:=numeric
axis 0 index values:=0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20
```

## 1. The time axis comes first

`sizes: 21 512 391 218` with `kinds: list domain domain domain` means **21 time
points of a 512×391×218 volume**, time leading. The `none` in `space directions`
is the list axis, which has no spatial extent.

**SimpleITK surfaces that leading `list` axis as vector components on a 3D
image**, not as a fourth dimension:

```python
image = sitk.ReadImage("TruncalValve_4DCT.seq.nrrd")
image.GetDimension()  # 3, not 4
image.GetNumberOfComponentsPerPixel()  # 21  <- the time points
image.GetSize()  # (512, 391, 218)
sitk.GetArrayFromImage(image).shape  # (218, 391, 512, 21)
```

So the frames arrive as the **last** axis of the array. `convert_nrrd` transposes
`(z, y, x, t)` to `(t, x, y, z)`.

Get that transpose wrong and you get 218 "time points" of a 21-slice volume. It
opens. It has a plausible affine. It is nonsense.

## 2. The handedness flips

The file declares `space: right-anterior-superior`. **SimpleITK normalizes every
image to LPS on read** and rewrites its own `NRRD_space` metadata to match — so
that key reports the space the data is in now, not the one the file declared.

NIfTI is RAS. So the affine has to be flipped back:

```python
lps = direction @ diag(spacing), with origin
affine = diag(-1, -1, 1, 1) @ lps
```

For the header above, that yields exactly the geometry the file declares:

```
[[0.5996, 0,      0,   -145.2  ],
 [0,      0.5996, 0,      8.9416],
 [0,      0,      0.7,  903.1   ],
 [0,      0,      0,      1     ]]
```

Getting this wrong mirrors the anatomy. Nothing raises. The volume opens, the
spacing is right, and left and right are swapped — which for a cardiac study is
the difference between the left and right ventricle.

### Voxel axes are not the same as the space

A NIfTI affine always maps into RAS *space*. `study.json.geometry.orientation`
records which way the **voxel axes** run within it, via `nibabel.aff2axcodes`. A
file declaring RAS comes out with axis codes `RAS`; one declaring LPS comes out
`LPS`. Both are correct, and neither is resampled.

**Do not reorient to canonical RAS.** The affine carries the orientation, and
resampling to make the axis codes read `RAS` introduces interpolation for no
benefit.

## Slicer sequence metadata

```
axis 0 index type:=numeric
axis 0 index values:=0 1 2 3 ... 20
```

These survive into SimpleITK metadata and become the time points' `phase_label`.
They are the only record of what the source called each frame.

Note what they are and are not: in this file they are **frame indices, not
measured R-R percentages**. If you write evenly spaced `phase_fraction` values
from them, say so in `study.json.notes` rather than presenting the even spacing
as if it were measured. The TruncalValve example does exactly that.

If the count of index values does not match the number of time points, they are
discarded rather than trusted — a mismatched label list is worse than none.

## Usage

```bash
# One 4D file
python -m openh4d.convert_nrrd TruncalValve_4DCT.seq.nrrd --out <root>/CHOP-0001A

# A directory of per-time-point 3D volumes
python -m openh4d.convert_nrrd frames/ --out <root>/STAN-0001A
```

Formats: `.nrrd`, `.nhdr`, `.mha`, `.mhd`, `.nii`, `.nii.gz`, `.vtk`.

For a directory, **files sorted by name become the time order**, and their names
become the phase labels. If your filenames do not sort into acquisition order —
`frame1, frame10, frame2` — zero-pad them first.

Every volume in a directory must share one voxel grid; a mismatch is refused
rather than resampled.

## Failure modes

**`holds a single volume, not a 4D sequence`** — a 3D file. If the time points
are separate files, point the tool at the directory containing them.

**`has size (...) but (...) has (...)`** — volumes in a directory are on
different grids. Fix at the source; resampling to agree hides which one is wrong.

**`SimpleITK could not read ...`** — not a format SimpleITK handles, or a
corrupted file. Note `.seq.nrrd` is just a naming convention; the file is an
ordinary NRRD.

## A check worth doing once

Open `t0000.nii.gz` alongside the original in 3D Slicer or ITK-SNAP and confirm
they overlay.

The unit tests check the computed affine against the geometry the header
declares, which catches an arithmetic error. Only a person looking at anatomy
catches a conceptual one.
