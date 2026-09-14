# OpenH-4D data card template

The data card is delivered as a **single `README.md` at the submission root**.
Hugging Face renders `README.md` as the dataset landing page
(<https://huggingface.co/docs/hub/datasets-cards>), so this is the one
user-facing document. There is no separate `DATA_CARD.md`.

Copy this, fill in every section, and delete nothing. A section that does not
apply says so — "N/A, no annotations in this release" tells a reader something;
a missing section leaves them wondering whether it was forgotten.

## YAML frontmatter (required)

The file must open with a YAML block between `---` fences. Hugging Face parses
this for the licence badge, search and modality filtering.

```yaml
---
pretty_name: "OpenH-4D -- <contributor / dataset name>"
license: cc-by-4.0
task_categories:
  - image-segmentation        # or another HF task, as appropriate
tags:
  - openh-4d
  - 4d
  - ct                        # ct | mri | ultrasound
  - heart                     # heart | lung
  - cardiac-motion            # cardiac-motion | respiratory-motion
language:
  - en
size_categories:
  - n<1K                      # or 1K<n<10K, 10K<n<100K
---
```

`license: cc-by-4.0` in the frontmatter is the load-bearing line for the Hugging
Face UI. A separate `LICENSE` file is still required, and OpenH-4D requires both.

Everything below lives in the **body**, after the closing `---`.

---

## Dataset Description

One or two paragraphs: what the data is, what motion it captures, what modality
and anatomy, and why the scans were acquired clinically. State plainly whether it
is clinical, phantom, simulated, or animal data.

The clinical justification matters more than it might seem — it is what tells a
downstream user which population the data represents, and the RFP asks for it
explicitly.

## Dataset Contributor(s)

Contributing organization and a contact who can answer questions about the data.

## Dataset Creation Date

MM/DD/YYYY for the OpenH-4D packaging. Do **not** put acquisition dates here:
those are HIPAA Safe Harbor identifiers.

## License / Terms of Use

OpenH-4D requires **CC BY 4.0**, which permits commercial use. Confirm the data
is cleared for it — IP, patient consent, institutional review. If your consent
language says "academic research only", that is incompatible and needs resolving
before submission, not after.

## Intended Usage

What downstream task this data supports: motion modelling, physics-informed
simulation, segmentation, digital twin construction, and so on.

## Dataset Characterization

- **Data Collection Method:** clinical / phantom / synthetic / animal
- **Labeling Method:** N/A, human-annotated, semi-automatic, derived
- **Acquisition system:** scanner make and model, and the imaging protocol
- **Gating:** retrospective ECG, prospective ECG, respiratory belt, navigator,
  surrogate marker, none

## Dataset Format

State which of the OpenH-4D forms you used and why:

- per-time-point `t0000.nii.gz` volumes (canonical)
- `dicom/t0000/` per-phase subdirectories (canonical)
- a single 4D `image4d.nii.gz` (accepted fallback)

Note any pre-processing applied before packaging: resampling, cropping,
intensity rescaling, defacing.

## Dataset Quantification

- Number of subjects, studies, and time points
- Total size on disk
- Typical matrix size and voxel spacing
- Typical number of time points per 4D sequence
- Train / validation / test split, if you are proposing one

## Subject Metadata

**Aggregate statistics only. No PHI.** Number of subjects, age range (capped at
89), sex distribution, disease/pathology distribution, scanner distribution.

If subject-level metadata is genuinely unavailable, say so here rather than
leaving `patient.json` sparse without explanation.

## Data Validation

Show that the submission holds together:

- `python -m openh4d.verify_layout <root>` reporting `compliant: true`
- `python -m openh4d.motion_report <study-dir>` output for a representative
  study, showing that the sequence actually moves
- One or two representative images, if you can share them

## Known Issues

Calibration quirks, missing fields, unit inconsistencies, artefacts, studies with
partial organ coverage, phases with degraded quality, anything a downstream user
would otherwise discover the hard way.

This section being empty is a warning sign, not a good sign. Real datasets have
quirks.

## Ethical Considerations

Consent status, de-identification approach and the standard applied, IRB or
equivalent approval — including explicit approval for **public release**, not
just for acquisition — and any usage caveats.
