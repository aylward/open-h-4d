# SPDX-License-Identifier: Apache-2.0
"""Platform-appropriate cache locations for OpenH-4D tooling.

Two things get cached outside the repository: the pinned ``dcm2niix`` binary,
and the re-identification crosswalks written by ``organize_dicom``. Crosswalks
in particular are cached here *deliberately* -- keeping them well away from the
submission tree means a ``zip -r`` of the submission's parent directory cannot
sweep one up by accident.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "openh-4d"

#: Set this to override every cache location (useful in CI and in tests).
CACHE_ENV_VAR = "OPENH4D_CACHE_DIR"


def cache_dir() -> Path:
    """Root cache directory, following each platform's convention."""
    override = os.environ.get(CACHE_ENV_VAR)
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME / "cache"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP_NAME

    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / APP_NAME


def dcm2niix_dir(tag: str) -> Path:
    """Directory holding the extracted ``dcm2niix`` build for release ``tag``."""
    return cache_dir() / "dcm2niix" / tag


def crosswalk_dir() -> Path:
    """Directory for re-identification crosswalks. Never inside a submission."""
    return cache_dir() / "crosswalk"


def examples_dir() -> Path:
    """Directory for downloaded example archives, so heavy runs download once."""
    return cache_dir() / "examples"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
