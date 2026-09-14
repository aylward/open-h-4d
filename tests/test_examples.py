# SPDX-License-Identifier: Apache-2.0
"""Smoke-tests that run each example script end to end.

Every script is executed in a subprocess so its side effects (generated files,
working-directory changes) stay isolated, and so a crash surfaces as a failed
exit code with its output attached rather than as an import-time surprise.

The split is by cost, not by kind:

* **light** -- no network. Runs on every pull request, on all three operating
  systems. This is where the DICOM round trip through dcm2niix is proved.
* **heavy** -- downloads gigabytes. Deselected by default; run on a schedule.

Run locally:

    uv run pytest tests/test_examples.py                  # light only
    uv run pytest tests/test_examples.py -m heavy         # the real datasets
    uv run pytest tests/test_examples.py -m ""            # everything
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from openh4d.verify_layout import verify

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"

LIGHT_EXAMPLES = [EXAMPLES / "synthetic-4d" / "build_example.py"]

HEAVY_EXAMPLES = [
    EXAMPLES / "tcia-4dlung" / "build_example.py",
    EXAMPLES / "truncalvalve-4dct" / "build_example.py",
]


def _id(path: Path) -> str:
    return str(path.relative_to(ROOT).as_posix())


def _run(script: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=3600,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _assert_clean_exit(script: Path, result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, (
        f"{script.name} failed (exit {result.returncode}).\n"
        f"--- stdout ---\n{result.stdout[-4000:]}\n"
        f"--- stderr ---\n{result.stderr[-4000:]}"
    )


# --- light -------------------------------------------------------------------


@pytest.mark.parametrize("script", LIGHT_EXAMPLES, ids=[_id(s) for s in LIGHT_EXAMPLES])
def test_light_example_builds_a_compliant_submission(script, tmp_path, dcm2niix_path):
    out = tmp_path / "submission"
    result = _run(
        script,
        ["--out", str(out), "--timepoints", "6", "--size", "12", "12", "6", "--offline"],
        cwd=tmp_path,
    )
    _assert_clean_exit(script, result)

    report = verify(out)
    assert report.compliant, report.errors
    assert report.summary["studies"] == 3
    assert report.summary["timepoints"] == 18


def test_synthetic_example_keeps_the_crosswalk_outside_the_submission(tmp_path, dcm2niix_path):
    """The one thing that must never ship with a submission."""
    out = tmp_path / "submission"
    _assert_clean_exit(
        LIGHT_EXAMPLES[0],
        _run(
            LIGHT_EXAMPLES[0],
            ["--out", str(out), "--timepoints", "4", "--size", "8", "8", "4", "--offline"],
            cwd=tmp_path,
        ),
    )
    assert not list(out.rglob("*crosswalk*"))
    assert (tmp_path / "submission-crosswalk.csv").is_file()


def test_synthetic_example_leaves_no_source_identifier_in_the_output(tmp_path, dcm2niix_path):
    out = tmp_path / "submission"
    _assert_clean_exit(
        LIGHT_EXAMPLES[0],
        _run(
            LIGHT_EXAMPLES[0],
            ["--out", str(out), "--timepoints", "4", "--size", "8", "8", "4", "--offline"],
            cwd=tmp_path,
        ),
    )
    for path in out.rglob("*"):
        if path.is_file():
            assert b"SRC-PATIENT" not in path.read_bytes(), f"source identifier leaked into {path}"


def test_synthetic_example_produces_measurable_motion(tmp_path, dcm2niix_path):
    """A submission that passes every structural check can still be static."""
    from openh4d.motion_report import build_report

    out = tmp_path / "submission"
    _assert_clean_exit(
        LIGHT_EXAMPLES[0],
        _run(
            LIGHT_EXAMPLES[0],
            ["--out", str(out), "--timepoints", "6", "--size", "12", "12", "6", "--offline"],
            cwd=tmp_path,
        ),
    )
    report = build_report(out / "SYNTH-0001A")
    assert report["has_motion"] is True
    assert report["duplicate_timepoints"] == []


def test_synthetic_example_data_card_has_hf_frontmatter(tmp_path, dcm2niix_path):
    """README.md is the data card; Hugging Face parses its frontmatter."""
    out = tmp_path / "submission"
    _assert_clean_exit(
        LIGHT_EXAMPLES[0],
        _run(
            LIGHT_EXAMPLES[0],
            ["--out", str(out), "--timepoints", "4", "--size", "8", "8", "4", "--offline"],
            cwd=tmp_path,
        ),
    )
    text = (out / "README.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "license: cc-by-4.0" in text
    assert "openh-4d" in text


# --- heavy -------------------------------------------------------------------


@pytest.mark.heavy
def test_tcia_4dlung_example(tmp_path):
    """Per-phase NIfTI in, compliant submission out, gate labels preserved."""
    script = EXAMPLES / "tcia-4dlung" / "build_example.py"
    out = tmp_path / "submission"
    _assert_clean_exit(
        script, _run(script, ["--out", str(out), "--max-patients", "1"], cwd=tmp_path)
    )

    report = verify(out)
    assert report.compliant, report.errors
    assert report.summary["studies"] == 1
    assert report.summary["timepoints"] == 10

    study = json.loads((out / "TCIA-0001A" / "study.json").read_text("utf-8"))
    assert study["organ"] == "lung"
    assert study["motion"] == "respiratory"
    assert [tp["phase_label"] for tp in study["timepoints"]] == [f"{p}%" for p in range(0, 100, 10)]
    assert [tp["phase_fraction"] for tp in study["timepoints"]] == [
        pytest.approx(p / 100) for p in range(0, 100, 10)
    ]


@pytest.mark.heavy
def test_truncalvalve_example(tmp_path):
    """A Slicer sequence in, 21 correctly oriented time points out."""
    import nibabel as nib

    script = EXAMPLES / "truncalvalve-4dct" / "build_example.py"
    out = tmp_path / "submission"
    _assert_clean_exit(script, _run(script, ["--out", str(out)], cwd=tmp_path))

    report = verify(out)
    assert report.compliant, report.errors
    assert report.summary["timepoints"] == 21

    study = json.loads((out / "CHOP-0001A" / "study.json").read_text("utf-8"))
    assert study["organ"] == "heart"
    assert study["motion"] == "cardiac"
    # The source declares RAS, so the voxel axes must come out RAS.
    assert study["geometry"]["orientation"] == "RAS"
    assert study["geometry"]["matrix"] == [512, 391, 218]
    assert study["geometry"]["in_plane_mm"][0] == pytest.approx(0.599609375, abs=1e-6)
    assert study["geometry"]["slice_spacing_mm"] == pytest.approx(0.7, abs=1e-6)

    affine = nib.load(str(out / "CHOP-0001A" / "t0000.nii.gz")).affine
    assert affine[0, 3] == pytest.approx(-145.19999694824219, abs=1e-3)


@pytest.mark.heavy
def test_heavy_examples_produce_measurable_motion(tmp_path):
    """Real 4D data must move, or the submission is 3D scans in a 4D wrapper."""
    from openh4d.motion_report import build_report

    script = EXAMPLES / "truncalvalve-4dct" / "build_example.py"
    out = tmp_path / "submission"
    _assert_clean_exit(script, _run(script, ["--out", str(out)], cwd=tmp_path))

    report = build_report(out / "CHOP-0001A")
    assert report["duplicate_timepoints"] == []
    assert report["n_timepoints"] == 21
