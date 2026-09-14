# OpenH-4D examples

Three worked examples, each building a complete, verifiable OpenH-4D submission
from a different kind of starting point. Copy the one closest to your data.

| Example | Starting point | Download | Runs in CI |
|---|---|---|---|
| [`synthetic-4d/`](synthetic-4d/) | A DICOM export, like a PACS hands you | none | yes |
| [`tcia-4dlung/`](tcia-4dlung/) | Per-phase NIfTI you already have | ~2.0 GB | no (`heavy`) |
| [`truncalvalve-4dct/`](truncalvalve-4dct/) | A 3D Slicer 4D sequence (`.seq.nrrd`) | ~1.3 GB | no (`heavy`) |

Each is a single script:

```bash
uv run python examples/synthetic-4d/build_example.py --out /tmp/oh4d-synthetic
```

Every script ends by running `openh4d.verify_layout` and exits non-zero if the
submission it just built does not pass. That is the point of them: they are
executable documentation of what a valid submission looks like, and they fail
loudly if the layout and the tooling drift apart.

Downloads are cached under the OpenH-4D cache directory
(`python -m openh4d.fetch_dcm2niix --show-cache`), so re-running an example
costs nothing after the first time.

## Start here

`synthetic-4d` needs no network and finishes in seconds. Run it first to check
your environment, and read it to see the whole pipeline in one file:

```
synthetic DICOM -> index -> PHI pre-flight -> organize -> convert -> verify
```

## What each one is actually for

**`synthetic-4d`** is the only example that exercises dcm2niix, so it is the one
that proves the DICOM path works on your machine. The data is a moving sphere,
which means the motion checks have real signal to measure rather than passing
vacuously.

**`tcia-4dlung`** covers the case where the data never went through a PACS. The
archive is already NIfTI: eight subjects, ten respiratory gates each, named
`_g000` through `_g090`. The interesting work is preserving what those gate
numbers mean, because that fraction is the only record of where in the breathing
cycle each volume sits.

**`truncalvalve-4dct`** covers a research tool's own format. Two things about a
Slicer sequence are silent failures if handled wrongly, and this example is the
end-to-end check that they stay handled: the time axis comes first, and the file
declares RAS while SimpleITK normalizes to LPS.
