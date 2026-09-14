# OpenH-4D RFP summary

Condensed from `assets/OpenH-4D_Request_For_Proposals.pdf`. The PDF is
authoritative; this is what the skills read so they do not have to parse it.

## What OpenH-4D is

A collaborative collection of **4D cardiac and respiratory imaging**, building on
the OpenH-Embodiment video collection initiative. The goal is 4D data from at
least **20,000 subjects** — patient details plus sequences of 3D images capturing
cardiac and/or respiratory motion — used to train and validate physics-informed
and generative AI models of physiological motion.

NVIDIA sponsors OpenH-4D by pre-processing all submissions and enabling free and
open access to the dataset, foundation model weights, and training code.

Released on Hugging Face under **CC BY 4.0**.

## Scope

Participating teams may submit 4D **CT, MRI, and Ultrasound** imaging for one or
both task groups:

- **Cardiac motion**
- **Lung respiratory motion**

The organizers anticipate expanding later to other modalities (dynamic x-ray
fluoroscopy) and other anatomies and functions (brain perfusion). A submission
outside the current scope is worth flagging for steering-group review rather
than rejecting outright.

## Targets

- At least 20,000 sequences of high-quality 4D CT, MRI or Ultrasound.
- A 40/40/20 split across CT, MRI and ultrasound.
- A 50/50 split between lung and cardiac.

These are corpus-level targets for the steering group's acceptance decisions.
They are **not** a bar any individual submission is evaluated against.

## What a proposal must contain (§8)

**Executive summary** — 500 words or less, plus a table giving, per contribution:
organ (heart/lung), the typical clinical justification for the acquisition
(e.g. lung cancer treatment planning), the relevant 4D imaging protocol
(e.g. MRI cine), number of patients, typical number of time points per 4D
sequence, and typical imaging resolution.

**Data details** — imaging format (DICOM or other), number of time points,
coverage of the organ of interest (full or partial), contrast (yes/no), and
annotations (segmentations, landmarks, categorizations).

**Modality specifics**

| Modality | Required |
|---|---|
| CT | low dose yes/no; in-plane resolution 1 mm or less; slice spacing 2 mm or less (cardiac), 3 mm or less (lung) |
| MRI | cine resolution; other sequences included |
| Ultrasound | source: 4D TEE, 4D percutaneous, or other |

**Patient information** — demographics (age, gender); disease state; vital signs
at acquisition (blood pressure, respiratory rate, pulse); relevant treatment
history (e.g. post-surgery); reason for scan (screening, surgical planning).

**Approvals** — privacy safeguards; IRB and/or other institutional documentation
approving acquisition *and* release; confirmation of approval for public release
under CC BY.

**Team qualifications, author list and citation, schedule.**

## Review criteria

1. Completeness of the application
2. Uniqueness of the data
3. Size and consistency of the data
4. Suitability of the data for the proposed AI foundation models

## Governance, ethics and compliance (§10)

- **Patient privacy** — all human subject data must be de-identified to **HIPAA
  Safe Harbor** standards or equivalent.
- **Regulatory** — proposers are responsible for obtaining IRB/ethics approval
  where required.
- **Licensing** — final datasets released under CC BY 4.0; all assets must be
  free of third-party IP encumbrances.

## Contribution benefits (§9)

Contributing teams are named co-authors on both the OpenH-4D dataset publication
and the follow-up publication describing the resulting foundation models. They
also receive early access to evaluation checkpoints and to the dataset itself for
their own downstream experiments, plus recognition at the joint releases.

## Eligibility

Academic institutions, startups and industrial healthcare firms. International
participation encouraged. Consortia may submit a joint proposal with a single
lead.

## Steering group

| Role | Name | Affiliation |
|---|---|---|
| Academic Lead | Alison Marsden, PhD | Stanford |
| Academic Lead | Paul Segars, PhD | Duke University |
| Generative AI Lead | Can Zhao, PhD | NVIDIA |
| Industry Lead | Mark Palmer, MD, PhD | Ansys, part of Synopsys |
| Clinical Lead | Matthew Jolley, MD | |
| Co-Chair | Mathias Ubernath, PhD | |
| Co-Chair | Stephen Aylward, PhD | NVIDIA |

Founding members: Ryan Moore, MD (Cincinnati Children's Hospital Medical
Center); Jeff Bodner, PhD (Medtronic).

Several roles are still listed as `XXXX` in the RFP and are not yet filled.

## Timeline

| Milestone | Date |
|---|---|
| RFP released | September 27, 2026 |
| Proposal submission deadline | November 1, 2026 |
| Data collection window | October 1 – December 31, 2027 |
| Data cleanup / standardization | January 1 – February 1, 2027 |
| Model training and validation | February 1 – March 15, 2027 |
| First public release of dataset and models | April 2027 |

> **These dates are internally inconsistent in the source PDF and are not yet
> settled.** Collection is listed as running through December 2027 while cleanup,
> training and release all fall in early 2027 — and §6 separately promises release
> "no later than March 2027", which contradicts the April 2027 date independently
> of the year problem. At least one year is a typo.
>
> Do not resolve this by picking one. If a date matters to a decision, say that
> the published timeline is inconsistent and needs confirmation from the RFP
> authors.

## Contact

- Technical questions: `openh.data+4d@gmail.com`
- Administrative questions: `saylward@nvidia.com`

The proposal submission form URL is still `XXXXXXXXXX` in the RFP and has not
been published.
