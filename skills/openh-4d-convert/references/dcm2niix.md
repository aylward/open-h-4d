# dcm2niix in OpenH-4D

## Why it is pinned

OpenH-4D converts every DICOM contribution with one pinned `dcm2niix` release.
Different versions can differ in how they resolve NIfTI orientation from DICOM
geometry. That difference does not raise anything — the volume opens, the spacing
is right — and a corpus assembled from several versions would carry an
inconsistency nobody could see and nobody could later untangle.

The pin lives in `src/openh4d/dcm2niix_pins.json`: the release tag, and for each
platform the asset name, size and SHA-256.

## Getting it

```bash
python -m openh4d.fetch_dcm2niix --print-path
```

Resolution order:

1. `--dcm2niix PATH` or `$OPENH4D_DCM2NIIX` — used verbatim, no checks. An
   explicit override is a deliberate act.
2. `dcm2niix` on `PATH`, **if** its build stamp is at least the pinned date.
   Older builds print a note and fall through; `--allow-any-dcm2niix` forces the
   system one.
3. The extracted binary in the cache.
4. Download, verify against the pin, extract, `chmod +x` on POSIX.

| Flag | Effect |
|---|---|
| `--print-path` | print the resolved path (the default action) |
| `--ignore-system` | skip anything on `PATH`, use the pinned build |
| `--offline` | never download; fail with instructions |
| `--show-cache` | print the cache directory |
| `--update-pins --all-platforms` | maintainers only; regenerate the pin file |

## Cache locations

| Platform | Directory |
|---|---|
| Windows | `%LOCALAPPDATA%\openh-4d\cache\` |
| macOS | `~/Library/Caches/openh-4d/` |
| Linux | `${XDG_CACHE_HOME:-~/.cache}/openh-4d/` |

`$OPENH4D_CACHE_DIR` overrides all three.

## Integrity

The dcm2niix project publishes no checksums, and GitHub release assets are
mutable — an asset can be replaced under an existing tag. So OpenH-4D ships its
own pins and refuses any download whose SHA-256 or size does not match:

```
error: dcm2niix_win.zip SHA-256 is <actual> but the pin expects <expected>.
GitHub release assets are mutable, so this means the file changed since the pin
was recorded. Do not use it.
```

If you see this, it is worth investigating rather than working around. Someone
replaced the asset. A maintainer regenerates the pins with
`--update-pins --all-platforms`, and the resulting diff is reviewed in a pull
request — which turns an invisible supply-chain risk into a visible one.

Extraction rejects archive members that resolve outside the target directory or
that are symlinks.

## Offline

`--offline`, or `$OPENH4D_OFFLINE=1`. Fails with everything needed to do it by
hand:

```
error: dcm2niix v1.0.20260724 is not available and downloads are disabled.
  Download: https://github.com/rordenlab/dcm2niix/releases/download/v1.0.20260724/dcm2niix_win.zip
  Expected SHA-256: 8310549ef7340b1b169c26f4721aeac69423fd73643fbbd22eec47b36cc70ada
  Extract it into: C:\Users\...\AppData\Local\openh-4d\cache\dcm2niix\v1.0.20260724\win
Or point $OPENH4D_DCM2NIIX at an existing binary.
```

## The arguments OpenH-4D uses

Held constant for every conversion:

| Flag | Why |
|---|---|
| `-z y` | gzip the output; `.nii.gz` is the OpenH-4D form |
| `-b y` | write the BIDS-style JSON sidecar, which carries acquisition detail worth keeping beside the volume |
| `-w 1` | overwrite without prompting, so a re-run is not interactive |
| `-f <name>` | output filename template |
| `-o <dir>` | output directory (a scratch dir; files are renamed to `t####.nii.gz` after) |

They are recorded in `study.json.source.converter_args`, so a reader knows
exactly how a volume was produced.

## Warnings worth reading

Collected into the conversion report and appended to `study.json.notes`, rather
than left in a log, because several of them change whether a study is usable.

**Non-uniform slice spacing.** dcm2niix may interpolate onto a regular grid.
Decide whether that is acceptable — sometimes the source genuinely has a gap, and
interpolating across it invents data.

**Gantry tilt.** dcm2niix corrects it, which resamples the volume. Fine, but note
it in the data card: a downstream user comparing against the original DICOM will
see a difference and wonder.

**Missing slices.** Usually an incomplete transfer. Re-export rather than
accepting a volume with a hole in it.

**Unable to determine manufacturer.** dcm2niix tunes some behaviour per vendor.
On synthetic data this is expected; on real data it suggests the `Manufacturer`
tag was stripped somewhere upstream.

**Patient Position not specified.** `PatientPosition` (0018,5100) is missing, so
dcm2niix cannot be sure of the patient orientation convention. Worth chasing on
real data.

**Multiple series in one phase directory.** dcm2niix split the input into several
volumes, which means a scout, a localizer, or a second reconstruction is mixed
into that time point. The largest output is kept and the rest are reported. Go
back and exclude them at the organize step rather than letting the size heuristic
decide.

## Multi-frame DICOM

dcm2niix understands Enhanced CT and MR and emits a single 4D NIfTI from a
container. `convert_dicom` then splits that into `t0000.nii.gz` onward with
nibabel, so the on-disk result is identical to the per-phase path.

## What dcm2niix cannot do

Vendor 4D ultrasound. Philips and GE store 4D echo in private tags, and dcm2niix
does not convert them meaningfully. See
[`us-vendor.md`](us-vendor.md) — that path is detected and routed before
dcm2niix is ever invoked.
