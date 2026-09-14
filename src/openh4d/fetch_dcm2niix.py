# SPDX-License-Identifier: Apache-2.0
"""Resolve a pinned ``dcm2niix`` binary, downloading it if necessary.

OpenH-4D converts DICOM to NIfTI with `dcm2niix <https://github.com/rordenlab/dcm2niix>`_,
pinned to one release so that 20,000 studies from dozens of contributors are all
converted by the same code. Different dcm2niix versions can differ in NIfTI
orientation handling; that difference is invisible in the output and poisons a
corpus quietly, which is exactly the failure worth spending a version check on.

Resolution order:

1. ``--dcm2niix PATH`` or ``$OPENH4D_DCM2NIIX`` -- used verbatim, no version
   check. An explicit override is a deliberate act.
2. ``dcm2niix`` on ``PATH``, if its build date is at least the pinned one.
   An older build falls through to the cache unless ``--allow-any-dcm2niix``.
3. The extracted binary in the cache directory.
4. Download, verify, extract, and use that.

Integrity: the release publishes no checksums, and GitHub release assets are
mutable, so this module ships its own pins in ``dcm2niix_pins.json`` and refuses
any download whose SHA-256 or size does not match. Regenerate the pins with
``--update-pins --all-platforms``; the resulting diff is reviewed in a pull
request, which turns an invisible supply-chain risk into a visible one.

Usage::

    python -m openh4d.fetch_dcm2niix --print-path
    python -m openh4d.fetch_dcm2niix --offline --print-path
    python -m openh4d.fetch_dcm2niix --update-pins --all-platforms
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .paths import cache_dir, dcm2niix_dir, ensure_dir

PINS_PATH = Path(__file__).parent / "dcm2niix_pins.json"

RELEASE_URL_TEMPLATE = "https://github.com/rordenlab/dcm2niix/releases/download/{tag}/{asset}"

#: Environment variables honoured by this module.
ENV_BINARY = "OPENH4D_DCM2NIIX"
ENV_OFFLINE = "OPENH4D_OFFLINE"

DOWNLOAD_TIMEOUT_S = 120

_VERSION_RE = re.compile(r"v?1\.0\.(\d{8})")


class Dcm2niixError(RuntimeError):
    """dcm2niix could not be resolved. The message says what to do about it."""


@dataclass(frozen=True)
class AssetPin:
    """The expected identity of one platform's release asset."""

    name: str
    sha256: str
    size: int


def platform_key() -> str:
    """``win``, ``lnx`` or ``mac`` -- the suffixes the release uses."""
    if os.name == "nt":
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "lnx"


def binary_name() -> str:
    return "dcm2niix.exe" if os.name == "nt" else "dcm2niix"


def load_pins() -> dict:
    if not PINS_PATH.is_file():
        raise Dcm2niixError(
            f"{PINS_PATH} is missing. Regenerate it with:\n"
            f"  python -m openh4d.fetch_dcm2niix --update-pins --all-platforms"
        )
    return json.loads(PINS_PATH.read_text(encoding="utf-8"))


def pinned_tag() -> str:
    return load_pins()["tag"]


def asset_pin(key: str | None = None) -> AssetPin:
    pins = load_pins()
    key = key or platform_key()
    try:
        entry = pins["assets"][key]
    except KeyError as exc:
        raise Dcm2niixError(
            f"no dcm2niix pin recorded for platform {key!r}. Known: "
            f"{sorted(pins.get('assets', {}))}"
        ) from exc
    return AssetPin(name=entry["name"], sha256=entry["sha256"], size=int(entry["size"]))


def asset_url(key: str | None = None, tag: str | None = None) -> str:
    pins = load_pins()
    return RELEASE_URL_TEMPLATE.format(tag=tag or pins["tag"], asset=asset_pin(key).name)


def is_offline(explicit: bool = False) -> bool:
    if explicit:
        return True
    return os.environ.get(ENV_OFFLINE, "").strip().lower() in {"1", "true", "yes"}


# --- version ------------------------------------------------------------------


def build_date(binary: Path) -> str | None:
    """Parse the ``1.0.YYYYMMDD`` build stamp from ``dcm2niix --version``.

    Returns ``None`` if the binary does not run or prints nothing recognisable.
    """
    try:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _VERSION_RE.search(f"{result.stdout}\n{result.stderr}")
    return match.group(1) if match else None


def pinned_build_date() -> str:
    match = _VERSION_RE.search(pinned_tag())
    if not match:
        raise Dcm2niixError(f"pinned tag {pinned_tag()!r} is not of the form v1.0.YYYYMMDD")
    return match.group(1)


# --- download -----------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_asset(key: str | None = None, tag: str | None = None) -> bytes:
    """Fetch a release asset into memory. ~5 MB, so this is fine."""
    import requests

    url = asset_url(key, tag)
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S, stream=True)
    response.raise_for_status()
    return response.content


def verify_asset(data: bytes, pin: AssetPin) -> None:
    """Raise unless ``data`` matches the committed pin exactly."""
    actual_size = len(data)
    if actual_size != pin.size:
        raise Dcm2niixError(
            f"{pin.name} is {actual_size} bytes but the pin expects {pin.size}. The release "
            f"asset may have been replaced. Do not use it; investigate, then regenerate the "
            f"pins with --update-pins if the change is legitimate."
        )
    actual_sha = _sha256_bytes(data)
    if actual_sha != pin.sha256:
        raise Dcm2niixError(
            f"{pin.name} SHA-256 is {actual_sha} but the pin expects {pin.sha256}. GitHub "
            f"release assets are mutable, so this means the file changed since the pin was "
            f"recorded. Do not use it."
        )


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    """Extract, rejecting members that escape ``target`` or that are symlinks.

    A zip is attacker-controlled data even when it comes from a trusted project,
    and ``ZipFile.extractall`` will happily write outside the destination.
    """
    target = target.resolve()
    for member in archive.infolist():
        destination = (target / member.filename).resolve()
        if not destination.is_relative_to(target):
            raise Dcm2niixError(
                f"refusing to extract {member.filename!r}: it resolves outside {target}"
            )
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise Dcm2niixError(f"refusing to extract symlink member {member.filename!r}")
    archive.extractall(target)


def extract(data: bytes, target: Path) -> Path:
    """Extract the archive into ``target`` and return the binary path."""
    import io

    ensure_dir(target)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        _safe_extract(archive, target)

    binary = _find_binary(target)
    if binary is None:
        raise Dcm2niixError(f"no {binary_name()} found after extracting into {target}")
    if os.name != "nt":
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


def _find_binary(root: Path) -> Path | None:
    """The archive layout varies between releases, so search rather than assume."""
    name = binary_name()
    direct = root / name
    if direct.is_file():
        return direct
    for path in sorted(root.rglob(name)):
        if path.is_file():
            return path
    return None


# --- resolution ---------------------------------------------------------------


def cached_binary(tag: str | None = None) -> Path | None:
    """The extracted pinned binary, if it is already in the cache."""
    target = dcm2niix_dir(tag or pinned_tag()) / platform_key()
    if not target.is_dir():
        return None
    return _find_binary(target)


def resolve(
    *,
    explicit: str | os.PathLike[str] | None = None,
    allow_any_system: bool = False,
    offline: bool = False,
    prefer_system: bool = True,
) -> Path:
    """Return a usable ``dcm2niix`` path, downloading it if allowed and needed."""
    explicit = explicit or os.environ.get(ENV_BINARY) or None
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise Dcm2niixError(f"{path} does not exist (from --dcm2niix or ${ENV_BINARY})")
        return path

    tag = pinned_tag()

    if prefer_system:
        system = shutil.which("dcm2niix")
        if system:
            system_path = Path(system)
            found = build_date(system_path)
            if allow_any_system or (found is not None and found >= pinned_build_date()):
                return system_path
            print(
                f"note: {system_path} reports build {found or 'unknown'}, older than the "
                f"pinned {pinned_build_date()}. Falling back to the pinned build; pass "
                f"--allow-any-dcm2niix to use the system one anyway.",
                file=sys.stderr,
            )

    cached = cached_binary(tag)
    if cached is not None:
        return cached

    if is_offline(offline):
        pin = asset_pin()
        target = dcm2niix_dir(tag) / platform_key()
        raise Dcm2niixError(
            f"dcm2niix {tag} is not available and downloads are disabled.\n"
            f"  Download: {asset_url()}\n"
            f"  Expected SHA-256: {pin.sha256}\n"
            f"  Extract it into: {target}\n"
            f"Or point ${ENV_BINARY} at an existing binary."
        )

    pin = asset_pin()
    data = download_asset()
    verify_asset(data, pin)
    return extract(data, dcm2niix_dir(tag) / platform_key())


# --- pin maintenance ----------------------------------------------------------


def update_pins(tag: str, keys: list[str]) -> dict:
    """Download each asset and record its name, size and SHA-256."""
    import requests

    assets = {}
    for key in keys:
        name = f"dcm2niix_{key}.zip"
        url = RELEASE_URL_TEMPLATE.format(tag=tag, asset=name)
        response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S)
        response.raise_for_status()
        data = response.content
        assets[key] = {"name": name, "sha256": _sha256_bytes(data), "size": len(data)}
        print(f"  {name}: {len(data)} bytes, sha256 {assets[key]['sha256']}", file=sys.stderr)
    return {
        "tag": tag,
        "source": "https://github.com/rordenlab/dcm2niix/releases",
        "note": (
            "Regenerate with: python -m openh4d.fetch_dcm2niix --update-pins --all-platforms "
            "--tag <tag>. Review the diff in a pull request -- GitHub release assets are "
            "mutable, so a change here is a signal, not a formality."
        ),
        "assets": assets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dcm2niix", help="use this binary verbatim, skipping every check")
    parser.add_argument(
        "--print-path", action="store_true", help="print the resolved path (the default action)"
    )
    parser.add_argument(
        "--allow-any-dcm2niix",
        action="store_true",
        help="accept a dcm2niix on PATH even if it is older than the pinned build",
    )
    parser.add_argument(
        "--ignore-system",
        action="store_true",
        help="ignore any dcm2niix on PATH and use the pinned build",
    )
    parser.add_argument("--offline", action="store_true", help="never download")
    parser.add_argument(
        "--update-pins", action="store_true", help="regenerate dcm2niix_pins.json (maintainers)"
    )
    parser.add_argument("--all-platforms", action="store_true", help="with --update-pins")
    parser.add_argument("--tag", help="release tag, with --update-pins")
    parser.add_argument("--show-cache", action="store_true", help="print the cache directory")
    args = parser.parse_args(argv)

    if args.show_cache:
        print(cache_dir())
        return 0

    if args.update_pins:
        tag = args.tag or pinned_tag()
        keys = ["win", "lnx", "mac"] if args.all_platforms else [platform_key()]
        print(f"downloading dcm2niix {tag} assets to compute pins...", file=sys.stderr)
        pins = update_pins(tag, keys)
        PINS_PATH.write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {PINS_PATH}", file=sys.stderr)
        return 0

    try:
        path = resolve(
            explicit=args.dcm2niix,
            allow_any_system=args.allow_any_dcm2niix,
            offline=args.offline,
            prefer_system=not args.ignore_system,
        )
    except Dcm2niixError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
