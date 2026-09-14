# TCIA 4D-Lung example

Restructures the public TCIA 4D-Lung release into the OpenH-4D submission
layout. Eight subjects of respiratory-gated 4D CT, ten phases each.

```bash
uv run python examples/tcia-4dlung/build_example.py --out /tmp/oh4d-lung --max-patients 1
```

Downloads ~2.0 GB on first run and caches it.

## This archive is already NIfTI

`TCIA-4DLung-Part1.zip` does not contain DICOM. Each subject is a directory of
ten `.nii.gz` volumes:

```
100_HM10395/
├── 100_HM10395_g000.nii.gz    0% gate
├── 100_HM10395_g010.nii.gz    10% gate
│   ...
└── 100_HM10395_g090.nii.gz    90% gate
```

So this example covers the path a contributor takes when their data never went
through a PACS, or came back from a research pipeline as NIfTI. It does not
exercise dcm2niix — [`../synthetic-4d/`](../synthetic-4d/) does that.

## The interesting part

`g040` means the 40% respiratory gate. That fraction is the only record of where
in the breathing cycle each volume sits, and the converter numbers volumes
`t0000` onward without knowing it. So the example reads the gate out of each
filename and writes it back:

```json
{"index": 4, "file": "t0004.nii.gz", "phase_label": "40%",
 "phase_fraction": 0.4, "phase_type": "respiratory"}
```

Drop that step and you have ten volumes in an arbitrary order.

## Honest gaps

The upstream release publishes no subject-level metadata — no age, sex, vitals
or staging. Those fields are left `null` rather than guessed, and `patient.json`
says so. A real OpenH-4D submission is expected to fill them in.

CT dose is likewise unknown. The RFP asks every CT contribution to declare
whether the acquisition was low dose, so the gap is recorded as an explicit
**waiver** with a justification rather than answered with a guess. That is what
waivers are for: they turn a silent omission into something the steering group
can see.

## Options

| Flag | Effect |
|---|---|
| `--max-patients N` | stop after N subjects (0 = all 8) |
| `--cache DIR` | override the download cache location |

## Citation

Hugo, G. D., Weiss, E., Sleeman, W. C., Balik, S., Keall, P. J., Lu, J., &
Williamson, J. F. (2016). Data from 4D Lung Imaging of NSCLC Patients. The
Cancer Imaging Archive.
