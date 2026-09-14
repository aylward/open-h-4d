# SPDX-License-Identifier: Apache-2.0
"""Cached downloads for the worked examples.

The example datasets are gigabytes, so they are fetched once into the user's
cache directory and reused. Re-running an example, or running two examples that
share an archive, costs nothing after the first time.

Downloads land in a temporary file and are moved into place only once complete,
so an interrupted transfer never leaves a half-written archive that looks cached.

Usage::

    python -m openh4d.download <url> [--name FILE] [--extract]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from .paths import ensure_dir, examples_dir

CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60

ENV_OFFLINE = "OPENH4D_OFFLINE"


class DownloadError(RuntimeError):
    """A download could not be completed. The message says what to do about it."""


def filename_from_url(url: str) -> str:
    return unquote(Path(urlparse(url).path).name)


def is_offline() -> bool:
    return os.environ.get(ENV_OFFLINE, "").strip().lower() in {"1", "true", "yes"}


def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def fetch(
    url: str,
    *,
    name: str | None = None,
    cache_dir: Path | None = None,
    expected_size: int | None = None,
    progress: bool = True,
) -> Path:
    """Download ``url`` into the cache and return the local path.

    A cached file whose size matches ``expected_size`` is returned untouched.
    A cached file whose size does *not* match is re-downloaded, because a
    truncated archive from an interrupted transfer is the common failure and it
    is indistinguishable from a good one until something tries to read it.
    """
    target_dir = ensure_dir(Path(cache_dir) if cache_dir else examples_dir())
    target = target_dir / (name or filename_from_url(url))

    if target.is_file():
        actual = target.stat().st_size
        if expected_size is None or actual == expected_size:
            if progress:
                print(f"using cached {target} ({_format_bytes(actual)})", file=sys.stderr)
            return target
        print(
            f"cached {target.name} is {_format_bytes(actual)} but should be "
            f"{_format_bytes(expected_size)}; re-downloading",
            file=sys.stderr,
        )
        target.unlink()

    if is_offline():
        raise DownloadError(
            f"{target.name} is not cached and downloads are disabled.\n"
            f"  Download: {url}\n"
            f"  Place it at: {target}"
        )

    import requests

    partial = target.with_suffix(target.suffix + ".partial")
    try:
        with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT_S) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            if progress:
                print(
                    f"downloading {target.name} ({_format_bytes(total) if total else 'unknown'})"
                    f" to {target_dir}",
                    file=sys.stderr,
                )
            written = 0
            last_report = 0
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
                    handle.write(chunk)
                    written += len(chunk)
                    if progress and total and written - last_report > 100 * 1024 * 1024:
                        last_report = written
                        print(
                            f"  {_format_bytes(written)} / {_format_bytes(total)} "
                            f"({100 * written / total:.0f}%)",
                            file=sys.stderr,
                        )
    except Exception as exc:  # noqa: BLE001 - any transport failure is one message
        partial.unlink(missing_ok=True)
        raise DownloadError(f"could not download {url}: {exc}") from exc

    if expected_size is not None and written != expected_size:
        partial.unlink(missing_ok=True)
        raise DownloadError(
            f"{target.name} downloaded {written} bytes but {expected_size} were expected. "
            f"The release asset may have changed, or the transfer was truncated."
        )

    partial.replace(target)
    if progress:
        print(f"saved {target} ({_format_bytes(written)})", file=sys.stderr)
    return target


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    """Reject members that escape ``target`` or that are symlinks."""
    import stat as stat_module

    resolved_target = target.resolve()
    for member in archive.infolist():
        destination = (resolved_target / member.filename).resolve()
        if not destination.is_relative_to(resolved_target):
            raise DownloadError(
                f"refusing to extract {member.filename!r}: it resolves outside {resolved_target}"
            )
        if stat_module.S_ISLNK(member.external_attr >> 16):
            raise DownloadError(f"refusing to extract symlink member {member.filename!r}")
    archive.extractall(resolved_target)


def extract_zip(archive_path: Path, target: Path, *, force: bool = False) -> Path:
    """Extract a zip into ``target``, skipping the work if it is already there."""
    target = Path(target)
    marker = target / ".openh4d-extracted"

    if marker.is_file() and not force:
        print(f"using already-extracted {target}", file=sys.stderr)
        return target

    if target.exists() and force:
        shutil.rmtree(target)

    ensure_dir(target)
    print(f"extracting {archive_path.name} to {target}", file=sys.stderr)
    with zipfile.ZipFile(archive_path) as archive:
        _safe_extract(archive, target)
    marker.write_text(archive_path.name + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--name", help="filename to cache as")
    parser.add_argument("--size", type=int, help="expected size in bytes")
    parser.add_argument("--extract", type=Path, help="extract the zip into this directory")
    args = parser.parse_args(argv)

    try:
        path = fetch(args.url, name=args.name, expected_size=args.size)
        if args.extract:
            extract_zip(path, args.extract)
    except DownloadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
