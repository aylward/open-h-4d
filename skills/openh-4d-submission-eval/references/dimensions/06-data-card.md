# Dimension 6 — Data card and documentation

## Persona

Two people at once: a **technical writer** who checks that every required section
is there and says something, and a **first-time user** — a second-year graduate
student in medical imaging who has never spoken to the contributor and is reading
this cold. The writer catches omissions. The first-time user catches the sections
that are present, well-formatted, and useless.

The second one is the harder and more valuable read.

## The artifact

`README.md` at the submission root. It **is** the data card: Hugging Face renders
it as the dataset landing page, so it is the one user-facing document. There is
no separate `DATA_CARD.md` — if one exists, its content belongs merged into
`README.md` and the duplicate flagged.

Template:
[`../../../openh-4d-shared/data-card-template.md`](../../../openh-4d-shared/data-card-template.md).

## Part 1 — the writer's pass

### Frontmatter

A YAML block between `---` fences at the very top. Minimum:

```yaml
pretty_name: "OpenH-4D -- <name>"
license: cc-by-4.0
task_categories: [...]
tags: [openh-4d, 4d, <modality>, <organ>, ...]
```

`license: cc-by-4.0` is what drives the Hugging Face licence badge. Missing or
wrong is `major` — and cross-check it against dimension 7, because frontmatter
saying `cc-by-4.0` while the `LICENSE` file says something else is a real
contradiction, not a formatting slip.

Missing frontmatter entirely: `major`. The page will not render as a dataset card.

### Required sections

Dataset Description, Contributor(s), Creation Date, License / Terms of Use,
Intended Usage, Characterization, Format, Quantification, Subject Metadata, Data
Validation, Known Issues, Ethical Considerations.

| Situation | Severity |
|---|---|
| Section absent | `major` |
| Section present, contains template text or `TODO` or `REQUIRES_CONTRIBUTOR` | `major` |
| Section present, one line that restates its own heading | `minor` |
| Section present and substantive | no finding |

"N/A, no annotations in this release" is a **good** answer. It tells the reader
something. An absent section does not.

### Statistics must be aggregate

Subject Metadata carries counts and distributions, never per-subject rows. A
table with one line per patient is `blocker` — route it to dimension 5.

### Statistics must be true

Cross-check the numbers against `manifest.json`: subjects, studies, time points,
size on disk. A card claiming 200 subjects over a 40-subject submission is
`major`, and not a typo worth waving through — it usually means the card was
written for the proposal and never updated.

## Part 2 — the first-time user's pass

**The bar: can this person load a study, understand what the motion represents,
and use it for a task, within about fifteen minutes, without asking anything?**

Read it start to finish as though you know nothing. Note every place you stop.

Things that reliably stop people:

**No stated task.** "4D CT of the chest" does not say what it is *for*. Cardiac
motion modelling, respiratory gating research, and tumour-tracking validation
want different things from the same pixels.

**Unexplained acronyms on first use.** TEE, bSSFP, NSCLC, ITV. One expansion each.

**No orientation to the time axis.** How many time points, what cycle they span,
whether the labels are measured or assigned, whether the sequence closes. This is
a 4D dataset; the temporal axis is the point, and cards routinely describe the
spatial dimensions in detail and leave the temporal one implicit.

**No worked entry point.** The path to one study, and what a reader will find in
it. Three lines of code beats three paragraphs.

**Clinical context assumed.** Why were these scans acquired? A reader who does not
know that 4D CT is standard for radiotherapy planning cannot tell whether this is
a routine cohort or a selected one.

Report friction as findings: what stopped you, and where. `minor` each, `major`
if the accumulated friction means the fifteen-minute bar is not met.

## Part 3 — specific checks

### Data Validation

Should show the submission holds together: `verify_layout` output, a motion
report for a representative study, ideally an image. A card asserting validity
without showing any is `minor`.

### Known Issues

**An empty Known Issues section is a warning sign, not a good sign.** Real
datasets have quirks — a phase that reconstructed badly, a subject with partial
coverage, an intensity scaling oddity. An empty section means either nobody
looked or nobody wrote it down.

`minor`, phrased as a question rather than an accusation: ask what the known
quirks are.

If dimension 4 found sorting artefacts, or dimension 3 found waived thresholds,
those belong here. Undisclosed: `major`.

### Additions beyond the proposal

Content present but not proposed is **welcome** and must be **documented**. An
undocumented addition is `major` — a reader cannot use what the card does not
mention, and the steering group needs to know the corpus contains it.

### Reproducibility

Enough to regenerate: source, conversion tool and version, any preprocessing.
`study.json.source` carries most of this; the card should say it in prose.
`minor` if absent.

## Severity summary

| Severity | Findings |
|---|---|
| `blocker` | per-subject rows in Subject Metadata (also dimension 5) |
| `major` | missing or placeholder sections; missing or wrong frontmatter licence; statistics contradicting the manifest; undocumented additions; undisclosed known issues; fifteen-minute bar not met |
| `minor` | thin sections; unexplained acronyms; no worked entry point; empty Known Issues; no reproducibility detail; no validation evidence |
| `info` | formatting, prose style, heading order |

## Not your call

**Prose quality and markdown formatting.** `info` at most. The bar is whether a
reader can use the data, not whether the writing is elegant.

**Whether the licence is legally sound.** Dimension 7. You check the card says
`cc-by-4.0` and that it does not contradict itself.

**Whether the data is any good.** Dimensions 2, 3 and 4.

## Evidence to capture

- Section-by-section: present, substantive, placeholder, or absent.
- The frontmatter block verbatim.
- Card statistics beside the manifest's, side by side.
- Your list of friction points from the cold read, in the order you hit them.
