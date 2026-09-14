# Agents in this repo

Cross-agent instructions for AI tools (Claude Code, Codex CLI, OpenCode, Copilot
CLI, Gemini CLI) working on the OpenH-4D repository.

## The one structural idea

**All deterministic logic lives in the Python package `src/openh4d/`. Skill
directories contain prose and reference material only.**

Skills invoke logic as `python -m openh4d.<tool>`. Nothing executable lives under
`skills/`.

This is what makes every rule ruff-linted, unit-tested and importable, and it
means the verify skill and the submission evaluator share literally the same code
object rather than the same file path. It also keeps a skill directory pure text,
so the discovery mechanism below never has to carry executable content across a
symlink.

If you find yourself about to put a script in a skill directory, put it in
`src/openh4d/` and call it from the skill.

## Skills

Skills live at top level in [`skills/`](skills/) — an agent-agnostic location.
Each is a directory with a `SKILL.md` plus whatever `references/` it needs.

| Skill | Purpose |
|---|---|
| [`skills/openh-4d-organize-dicom/`](skills/openh-4d-organize-dicom/) | Split a DICOM pile into the OpenH-4D layout: group by patient, study and time point, assign de-identified identifiers, keep the crosswalk outside the submission |
| [`skills/openh-4d-convert/`](skills/openh-4d-convert/) | Convert to per-time-point NIfTI — DICOM via pinned dcm2niix, NRRD/Slicer sequences via SimpleITK, vendor 4D ultrasound via the private-tag decoder |
| [`skills/openh-4d-verify-layout/`](skills/openh-4d-verify-layout/) | Verify a submission against the layout specification and explain failures |
| [`skills/openh-4d-submission-eval/`](skills/openh-4d-submission-eval/) | Seven-dimension graded intake review producing `evaluation_report.md` |

[`skills/openh-4d-shared/`](skills/openh-4d-shared/) is **support material, not a
skill**. It has no `SKILL.md`, so it gets no discovery entry and no agent tries to
invoke it. It holds the normative layout specification, the RFP summary, the
sidecar templates, the PHI checklist and the evaluation report template, which
the four skills read by relative path (`../openh-4d-shared/layout-spec.md`).

### Discovery

Claude Code reads project skills from exactly `<repo>/.claude/skills`. That
directory is **generated, not committed**:

```bash
python scripts/bootstrap_skills.py
```

Required once per clone. Idempotent, standard library only, no admin rights and
no Developer Mode. It prints which mechanism it used per skill and exits non-zero
if any skill could not be surfaced.

For each `skills/<name>/` containing a `SKILL.md` it ensures
`.claude/skills/<name>` resolves there, trying: an existing working entry, then
`os.symlink`, then a Windows directory junction (`mklink /J`), then a plain copy
with a warning.

**Why it is not committed.** Committing symlinks does not survive a native
Windows clone: git without Developer Mode writes a plain text file containing the
path, and the Claude Code loader silently drops any entry that is neither a
directory nor a symlink — the skill disappears with no error anywhere. Committing
the junctions instead is worse, because git follows a junction and stores the
target's *contents*, so every skill file would be committed twice and the copies
could drift. Generating the directory is the only arrangement that works on all
three platforms.

`tests/test_skill_discovery.py` asserts every skill is reachable, so a clone that
skipped the bootstrap fails the test suite with a message naming the fix rather
than losing skills silently.

### Other tools

Read `skills/<name>/SKILL.md` directly, or point your tool's instruction config at
it. The content is portable markdown; only the `.claude/skills/` discovery step is
Claude Code specific.

## Conventions

- **Platforms:** Windows, Linux and macOS, natively. No WSL, no compiler, no GPU.
  CI proves all three on every pull request.
- **Environment:** `uv sync` (or `uv sync --extra test`). Plain `pip install -e .`
  also works.
- **Style:** ruff, line length 100. `uv run ruff format .` and `uv run ruff check .`
- **Tests:** `uv run pytest tests/ -m "not heavy"` for the light suite — no
  network, about 30 seconds. `-m heavy` downloads the real example datasets.
- **The prose spec and the code must agree.** `tests/test_spec_consistency.py`
  asserts the naming grammar in `skills/openh-4d-shared/layout-spec.md` is
  identical to the patterns compiled in `openh4d.naming`, that both sidecar
  templates validate, and that every conformance rule is documented. Editing a
  rule means editing both places; that friction is intentional.

## Things that are easy to get wrong here

**De-identification.** The tooling *verifies*; it does not *perform*. Do not add
anonymization. Do not soften the pre-flight refusal. Do not let a crosswalk near
a submission directory.

**Silent failures are the whole problem.** Most defects this repo guards against
produce files that open cleanly and are wrong: a transposed time axis, a flipped
handedness, a collapsed phase sort, an old dcm2niix with different orientation
handling. When adding a check, ask what it would look like if it were wrong, not
just whether it errors.

**Never invent clinical metadata.** `organ`, `motion`, `coverage`, `contrast`,
diagnosis, vitals and reason for scan are not in any header. The tooling leaves
them `null` and the verifier rejects the submission until a contributor fills
them in. A plausible guess is worse than a blank because it reads as an answer.

**Waivers are the mechanism for real gaps.** When data genuinely cannot meet an
RFP threshold, a waiver with a justification makes that visible to the steering
group. Do not treat a waived finding as evasion, and do not accept an empty
justification.

**Verify against real data before believing a converter.** The NRRD time-axis and
handedness behaviour in this repo was established by reading the header of the
actual 1.3 GB file, and the closed-cycle case in the motion report was found by
running against it. Synthetic fixtures confirm arithmetic; real files reveal
assumptions.
