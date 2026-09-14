# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures.

Tests run inside a temporary working directory so generated NIfTI/DICOM files
never land in the repo. ``REPO_ROOT`` is resolved at import time, before the
chdir, so tests that need repo paths still have them.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def run_in_tmpdir(tmp_path, monkeypatch):
    """Run every test inside a temporary directory."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def dcm2niix_path():
    """The pinned dcm2niix binary, or skip.

    Resolved offline so the suite never downloads during a test run. CI fetches
    it in an explicit step before pytest, which is also what warms the cache on a
    developer machine.
    """
    from openh4d.fetch_dcm2niix import Dcm2niixError, resolve

    try:
        return resolve(offline=True)
    except Dcm2niixError as exc:
        pytest.skip(f"dcm2niix not available offline: {exc}")
