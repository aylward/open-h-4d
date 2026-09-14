# OpenH-4D evaluation report template

The exact structure of `evaluation_report.md`. Follow it; a consistent report is
what lets the steering group compare submissions.

---

```markdown
# OpenH-4D Submission Evaluation: <submission name>

**Overall verdict:** Accept | Accept with revisions | Reject — needs resubmission
**Evaluated on:** <date>
**Reviewer:** Claude (automated)
**Submission root:** <path>
**Proposal:** <path or reference>

## Executive scorecard

| # | Category | Result |
|---|---|---|
| 1 | Layout & naming | PASS / FAIL |
| 2 | Image data integrity | PASS / FAIL |
| 3 | Metadata & protocol conformance | PASS / FAIL |
| 4 | Motion & temporal quality | PASS / FAIL |
| 5 | De-identification & PHI | PASS / FAIL |
| 6 | Data card & documentation | PASS / FAIL |
| 7 | Licensing, IRB & governance | PASS / FAIL |

A category is PASS if its worst severity is `info` or `minor`, FAIL if any
finding is `major` or `blocker`. Binary by design — the severity detail is
below, for reviewers who need it.

## At a glance

| | |
|---|---|
| Patients | N |
| Studies | N |
| Time points | N |
| Modalities | CT: N, MR: N, US: N |
| Organs | heart: N, lung: N |
| Size on disk | N GB |

## Proposal alignment

| Aspect | Status |
|---|---|
| Delivered as proposed | <short summary> |
| Under-delivered (promised but missing or smaller) | <list, or "none"> |
| Added beyond proposal (must be documented in the data card) | <list, or "none"> |
| Accepted carve-outs honoured | <list, or "none"> |

## Feedback for the contributor

One paragraph per FAILED category, 3–5 sentences, second person. What is wrong,
why it matters, and the concrete fix. For PASSED categories, one sentence only
if there is something worth saying.

**<N>. <Category> — FAIL.** ...

## Per-dimension findings

### 1. Layout & naming
**Status:** pass | pass_with_notes | fail | blocked
**Severity:** info | minor | major | blocker

**Findings**
- ...

**Evidence**
- ...

**Suggested fixes**
- ...

[repeat for dimensions 2 through 7]

## Auto-fixes applied

- <file>: <what changed and why>

Or: "None."

## Action items for the contributor

Blockers first, then majors, then minors. Each one specific enough to act on
without re-reading the report.

1. ...

## Notes for the steering group

Anything that is not a contributor action: accepted waivers and their
justifications, scope observations, corpus-balance contribution (modality and
organ, against the RFP's 40/40/20 and 50/50 targets), and anything unusual worth
a human decision.
```

---

## Verdict rule

- any category FAILS with `blocker` → **Reject — needs resubmission**
- any category FAILS with `major` → **Accept with revisions**
- all categories PASS → **Accept**

## Notes on filling this in

**"At a glance" comes from `manifest.json` and the verifier summary.** Do not
retype it from the data card; the point of having it here is that it is measured
rather than claimed.

**The feedback section is the most-read part.** Most contributors read it and
skip the per-dimension detail entirely. Put the effort there.

**"Notes for the steering group" is not a dumping ground.** It is for decisions a
human needs to make: a waiver worth accepting, data outside the current scope
that is nonetheless interesting, a submission that shifts the corpus balance. If
it is a contributor action, it belongs in action items.

**Corpus balance is informational.** The RFP's 40/40/20 modality and 50/50
lung/cardiac targets apply to the whole collection, not to any one submission.
Note what this submission contributes; never fail a submission for its mix.

**Where to put the report.** Beside the submission, not inside it — the
submission root is defined by the layout spec and an `evaluation_report.md` there
would be an unexpected entry. Auto-fixed files go in `autofix/` beside it.
