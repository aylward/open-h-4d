# An unfamiliar DICOM export

The fallback interview for a source with no per-format notes. Work through it
with the contributor before applying a plan.

## 1. Index first, ask second

```bash
python -m openh4d.dicom_index <source> --summary
```

Do not form a theory before reading this. It tells you how many patients and
studies it found, how many time points per study, which heuristic recovered them,
how confident it is, and what it could not place.

Exit code `2` means at least one study needs confirmation.

## 2. Establish what one study is

The single most consequential question, and the tags cannot answer it.

> Your export has 14 `StudyInstanceUID`s across 12 subjects. Two subjects have
> two studies each. Is that two separate 4D acquisitions per subject — a
> follow-up scan, or a breath-hold and a free-breathing run — or one acquisition
> the scanner split?

What to watch for:

- **One study, two acquisitions.** A breath-hold and a free-breathing run under
  one `StudyInstanceUID` are two OpenH-4D studies.
- **Two studies, one acquisition.** A scanner that started a new study partway
  through is one OpenH-4D study.
- **Repeat imaging across a treatment course.** Genuinely separate studies —
  `…A`, `…B`, `…C` by date. This is what the letter suffix is for.

## 3. Confirm the time-point recovery

The plan names the rung that fired. Ask about it in the contributor's terms, not
in tag names:

> I found 10 time points per study, from a phase percentage in the series
> description (`0.0%` through `90.0%`). Does that match how the data was
> acquired — ten respiratory phases of one breathing cycle?

If more than one rung fired with **different counts**, put both in front of them:

> Two signals disagree. The series descriptions say 10 phases;
> `AcquisitionNumber` says 2. That would be consistent with two acquisitions each
> reconstructed into 10 phases. Which is it?

**Never resolve this yourself.** Both answers are internally consistent.

If the rung is `InstanceNumber`, say plainly what that means:

> No temporal tag identified the time points, so I fell back to splitting by
> position in the instance sequence. That assumes the files were written
> volume-by-volume rather than interleaved. If that assumption is wrong the
> result will look completely normal and be wrong. Can you confirm the write
> order, or point me at where the phase information lives?

## 4. Ask about what could not be placed

Scouts, localizers, dose reports, structure sets, second reconstructions of the
same acquisition. The plan lists them. Ask which to exclude — and note that a
second reconstruction (a different kernel, say) is a judgement call, not obviously
excludable.

## 5. Check the geometry warnings

| Warning | Ask |
|---|---|
| Non-uniform slice spacing | is there a genuine gap in the acquisition, or is this a rounding artefact? |
| Duplicate slice positions within a time point | almost always two phases merged — re-examine the grouping |
| Differing slice counts across time points | which phases are short, and why? |

## 6. Multi-frame

If `NumberOfFrames` > 1, one file already holds every time point. The files go
directly in `dicom/` with no per-phase subdirectories, and `study.json` gets
`multiframe: true`. Nothing to confirm; just do not try to split it.

## 7. Ultrasound

If `Modality` is `US`, check whether it is vendor private-tag 4D echo. The convert
skill detects this automatically, but it is worth flagging early so the
contributor knows a workstation export may be needed. See
[`../../../openh-4d-convert/references/us-vendor.md`](../../../openh-4d-convert/references/us-vendor.md).

## 8. The clinical interview

None of this is in any header, and none of it may be invented:

- Which organ, and which motion — cardiac, respiratory, or both?
- Full or partial coverage of the organ of interest?
- Why were these acquired clinically?
- Contrast? Which agent, which phase?
- Low dose? (the RFP asks every CT to declare it)
- Demographics, disease state, vitals at acquisition, treatment history, reason
  for scan

Ask in batches. Accept "unknown" — then record it as a **waiver** with a
justification rather than guessing, so a real gap stays visible.

## 9. Which prefix

The steering group issues each contributor an identifier prefix so identifiers
stay unique when submissions are merged. Ask which one they were given. If they
do not have one, say the default `OH4D` is for local experiments and they should
request one before submitting.

## Writing it down

If the source turns out to have a distinctive shape — a vendor whose exports
always encode phase somewhere unexpected, a site with a consistent convention —
add a note under `references/sources/`. The next person converting the same kind
of data should not have to rediscover it.
