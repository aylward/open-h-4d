---
name: openh-4d-submission-eval
description: Evaluate a contributor submission to the OpenH-4D initiative against the RFP and the layout specification. Use whenever someone wants to review, grade, validate, accept, QA or intake a proposed contribution of 4D cardiac or respiratory imaging — including when they hand over a folder, a Hugging Face link, a data card, or a set of 4D CT/MRI/ultrasound studies and ask whether it is ready, whether it meets the bar, what is missing, or whether it can be accepted. Also use to auto-fix gaps such as a stale manifest or a missing motion report, and to produce the graded evaluation_report.md with an accept / accept-with-revisions / reject verdict. Trigger even if the user does not say OpenH-4D by name: 4D medical imaging plus a review or intake context is enough.
---

# OpenH-4D submission evaluation

Review a contributor submission across seven independent dimensions and produce a
graded acceptance report.

## What you are evaluating against

- [`../openh-4d-shared/rfp-summary.md`](../openh-4d-shared/rfp-summary.md) — scope, review criteria, governance, timeline
- [`../openh-4d-shared/layout-spec.md`](../openh-4d-shared/layout-spec.md) — the normative submission layout
- [`../openh-4d-shared/data-card-template.md`](../openh-4d-shared/data-card-template.md) — what `README.md` must contain
- [`../openh-4d-shared/phi-checklist.md`](../openh-4d-shared/phi-checklist.md) — HIPAA Safe Harbor identifiers and where they hide
- [`references/motion-artifacts.md`](references/motion-artifacts.md) — 4D artefact taxonomy, for dimension 4

Pull these in when a dimension calls for them. Do not try to hold them all at once.

## Sub-agent personas

Each dimension file opens with a short **Persona** — a role and a disposition.
When dispatching a sub-agent, that persona is the framing it works under. They
are deliberately light: enough to set rigor and tone, not a character.

Do not invent new personas or stretch them. If one feels wrong for a particular
submission, fall back to neutral instruction-following rather than drifting.

## Step 1 — inventory, and read the proposal

List what is actually there before evaluating anything. Expect:

- study directories `<PREFIX>-NNNNA/`, each with `study.json` and either
  per-time-point `.nii.gz`, a `dicom/` tree, or `image4d.nii.gz`
- `<PREFIX>-NNNN_ehr/patient.json` per patient
- `README.md` at the root — this **is** the data card
- `LICENSE` declaring CC BY 4.0
- `manifest.json`
- possibly IRB or consent documentation

Record it as `inventory.json`. If there is nothing evaluable — no studies at all —
stop and say so.

**Read the accepted proposal. It is required.** The proposal is the contract: a
submission is judged partly on whether it delivers what the contributor said it
would. Extract the promised modalities, anatomies and quantities, the licence
intent, the ethics commitments, and any **carve-outs** the steering group already
accepted.

**If no accepted proposal can be produced, stop and mark the evaluation
`blocked`.** Without it you cannot check alignment, and you will re-litigate
things that were already settled.

### Step 1b — compare the submission to the proposal

Three buckets:

- **Delivered as proposed** — no finding.
- **Under-delivered** — promised and missing or materially smaller, and not a
  carve-out. A `major` finding routed to the relevant dimension.
- **Added beyond the proposal** — present but not proposed. **Not a reason to
  reject**; additions are welcome. But each must be described in the data card,
  and an undocumented addition is a `major` data-card finding.

Accepted carve-outs are **not** failures. Pass the carve-out list into every
dimension sub-agent so nobody double-jeopardies the contributor on something
already negotiated.

Common carve-outs: "subject demographics aggregate-only", "no annotations in this
release", "acquisition parameters unavailable for the legacy cohort".

## Step 2 — run the deterministic checks first

They are cheap, they gate everything else, and they give the sub-agents evidence
rather than impressions:

```bash
python -m openh4d.verify_layout <root>                 # dimension 1
python -m openh4d.check_volumes <study-dir>            # dimension 2, per study
python -m openh4d.phi_scan <root> --mode submission    # dimension 5
python -m openh4d.motion_report <study-dir>            # dimension 4, per study
```

## Step 3 — dispatch the seven dimensions

One sub-agent per dimension, in parallel. Each reads only its own
`references/dimensions/0N-*.md` and only the artifacts that file names, and
returns:

```json
{"dimension": 1, "status": "pass", "severity": "info",
 "findings": [], "evidence": [], "suggested_fixes": []}
```

`status` ∈ `pass`, `pass_with_notes`, `fail`, `blocked` (could not evaluate —
usually a missing dependency).
`severity` ∈ `info`, `minor`, `major`, `blocker`. A `blocker` anywhere means the
submission cannot be accepted as-is.

| # | Dimension | Persona |
|---|---|---|
| 1 | [Layout & naming](references/dimensions/01-layout.md) | data-format auditor |
| 2 | [Image data integrity](references/dimensions/02-image-integrity.md) | medical-imaging data engineer |
| 3 | [Metadata & protocol conformance](references/dimensions/03-metadata.md) | imaging protocol reviewer |
| 4 | [Motion & temporal quality](references/dimensions/04-motion-quality.md) | 4D imaging physicist |
| 5 | [De-identification & PHI](references/dimensions/05-deidentification.md) | research compliance officer |
| 6 | [Data card & documentation](references/dimensions/06-data-card.md) | technical writer and first-time user |
| 7 | [Licensing, IRB & governance](references/dimensions/07-governance.md) | research compliance officer |

Without sub-agents, run them in order: 1 first (cheap, gates the rest), 4 last
(loads every voxel).

### Requirements with no carve-out

Enforced in code, so dimensions 1 and 5 catch them automatically:

- **At least 2 time points per study.** OpenH-4D collects 4D data.
- **Every patient has a `patient.json`.**
- **Zero PHI findings.** Any Safe Harbor identifier is a `blocker` regardless of
  what the proposal said.

## Step 4 — auto-fix what is safely fixable

| Fix | When |
|---|---|
| Regenerate `manifest.json` | missing, or counts drifted |
| Backfill `geometry` from NIfTI headers | `study.json` is stale and the header is right |
| Add motion-report output to the data card's Data Validation section | missing |
| Fill derivable data-card fields — counts, sizes, matrix, time points | missing; mark the rest `REQUIRES_CONTRIBUTOR` |

**Never auto-fix:** licence declarations, consent or IRB status, subject
metadata, clinical fields (`organ`, `motion`, `coverage`, `contrast`, diagnosis,
reason for scan), intended-usage text, or known issues. A plausible guess in any
of these is worse than a blank, because it reads as an answer.

**Never resolve a PHI finding by deleting the offending file.** That hides a
process problem. Report it.

Put auto-fixed files in `autofix/` beside the report so the contributor can see
exactly what changed.

## Step 5 — write the report

Use [`../openh-4d-shared/evaluation-report-template.md`](../openh-4d-shared/evaluation-report-template.md)
exactly. Write it to `evaluation_report.md` beside the submission.

**Pass/fail per category:** PASS if its worst severity is `info` or `minor`; FAIL
if any finding is `major` or `blocker`. Binary by design — the detailed severity
lives in the per-dimension section for reviewers who need it, and the contributor
sees pass or fail.

**Verdict:**

- any category FAILS with `blocker` → **Reject — needs resubmission**
- any category FAILS with `major` → **Accept with revisions**
- all PASS → **Accept**

## Writing the contributor feedback

The feedback paragraphs are the most-read part of the report. They are the only
part most contributors will read closely.

**Voice.** Address the contributor as "you". It is their dataset, their pipeline,
their data card. Colleague to colleague — not formal, not breezy.

**Structure**, one paragraph per FAILED category, 3–5 sentences: what is wrong,
in one sentence; why it matters for downstream users or for acceptance, in one;
the concrete fix, in one or two. If a category has several problems, take the one
or two most consequential — the rest live in the per-dimension detail.

**Plain language.** "Your time points are on different voxel grids, so voxel
(100, 100, 50) is not the same anatomy in phase 0 and phase 5" is good. "Affine
inconsistency across the temporal axis manifold" is not.

**No persona artifacts.** The compliance officer and the imaging physicist are
framings for sub-agents. The contributor reads one neutral voice.

For PASSED categories, one sentence only if there is something worth saying. Most
get nothing.

Example:

> **3. Metadata & protocol conformance — FAIL.** Your CT studies do not state
> whether they were acquired with a low-dose protocol, which the RFP asks every CT
> contribution to declare, and eleven of them have 1.4 mm in-plane resolution
> against the 1.0 mm requirement. The resolution matters because the foundation
> model training assumes a consistent spatial scale across the corpus. Set
> `ct.low_dose` in each `study.json`; for the resolution, either supply a thinner
> reconstruction if one exists, or add a waiver to those eleven studies explaining
> why it does not — a waiver with a real justification is a normal outcome here,
> not a black mark.

## Judgement calls

**"A novice user" means** a graduate student in medical imaging who has not
spoken to the contributor and is reading the data card cold. Not a beginner —
they know what a 4D acquisition is. The bar is: can they load a study, understand
what the motion represents, and use it for a task within about fifteen minutes.

**Corpus targets are not a submission bar.** The RFP wants a 40/40/20
CT/MRI/ultrasound and 50/50 lung/cardiac split *across the whole corpus*. That is
a steering-group acceptance-priority matter. Never fail a submission for being
CT, or for being cardiac.

**Waivers are a normal outcome.** A study outside an RFP threshold with a real
justification recorded is working as designed. Note it for the steering group;
do not treat it as evasion. An *unjustified* waiver is a different matter — that
is a `major` finding.

**Do not reject for style.** Markdown formatting, prose quality, variable names:
`info` at most. The bar is technical correctness and completeness.

**Scope surprises are `info`.** A submission outside the current cardiac/lung
scope is flagged for steering-group review, not failed — the RFP anticipates
expanding.

**Empty Known Issues is a warning sign.** Real datasets have quirks. Worth a
`minor` finding asking what they are.

**The RFP timeline is internally inconsistent** and not yet settled. If a date
bears on your evaluation, say the published timeline is contradictory and needs
confirming from the authors. Do not pick one.
