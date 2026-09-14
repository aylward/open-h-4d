# Synthetic 4D CT example

Builds a complete OpenH-4D submission from synthetic DICOM, with no network
access and in a few seconds. Run this first.

```bash
uv run python examples/synthetic-4d/build_example.py --out /tmp/oh4d-synthetic
```

## What it does

```
synthetic DICOM  ->  index  ->  PHI pre-flight  ->  organize
                 ->  convert (dcm2niix)  ->  verify
```

Every step is the same code a real contributor runs. The only thing that is not
real is the data: a sphere translating on a closed loop, written as DICOM by
`openh4d.synthetic_dicom`.

The sphere matters. A generator that emitted noise, or a static shape, would let
the motion checks pass without measuring anything.

## What it produces

```
/tmp/oh4d-synthetic/
├── README.md              the data card
├── LICENSE                CC BY 4.0
├── manifest.json
├── SYNTH-0001A/           first study of the first subject
│   ├── study.json
│   ├── t0000.nii.gz ...   one volume per time point
│   └── dicom/t0000/ ...   the source DICOM, retained
├── SYNTH-0001B/           a second study from the same subject
├── SYNTH-0001_ehr/patient.json
├── SYNTH-0002A/
└── SYNTH-0002_ehr/patient.json
```

Note `SYNTH-0001A` and `SYNTH-0001B`: the letter suffix always starts at `A`,
even for a subject with only one study, so adding a second one later never
renames the first.

The identifier crosswalk is written *outside* the submission, beside it rather
than in it. That is deliberate and enforced — the verifier errors on any file
inside a submission whose name looks like a crosswalk.

## Options

| Flag | Effect |
|---|---|
| `--timepoints N` | time points per study (default 10) |
| `--size NX NY NZ` | volume dimensions (default 24 24 12) |
| `--drop-dicom` | delete the DICOM after converting, leaving only NIfTI |
| `--offline` | never download dcm2niix; fail with instructions instead |

## Requirements

dcm2niix, which `openh4d.fetch_dcm2niix` downloads and caches automatically on
first use. To check it resolves:

```bash
uv run python -m openh4d.fetch_dcm2niix --print-path
```
