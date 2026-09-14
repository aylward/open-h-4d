# SPDX-License-Identifier: Apache-2.0
"""Resolving the pinned dcm2niix binary.

Everything here runs with the network monkeypatched, so the suite stays fast and
works offline. The one test that really downloads is marked ``network`` and is
deselected by default.
"""

import hashlib
import io
import os
import zipfile

import pytest

from openh4d import fetch_dcm2niix as fetch
from openh4d.fetch_dcm2niix import AssetPin, Dcm2niixError

BINARY = fetch.binary_name()


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Never touch the developer's real cache."""
    monkeypatch.setenv("OPENH4D_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv(fetch.ENV_BINARY, raising=False)
    monkeypatch.delenv(fetch.ENV_OFFLINE, raising=False)
    return tmp_path / "cache"


def make_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def pin_for(data: bytes, name: str = "dcm2niix_test.zip") -> AssetPin:
    return AssetPin(name=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data))


# --- pins --------------------------------------------------------------------


def test_pins_file_covers_all_three_platforms():
    """A contributor on any of the three supported platforms must be able to fetch."""
    pins = fetch.load_pins()
    assert set(pins["assets"]) == {"win", "lnx", "mac"}
    for key, entry in pins["assets"].items():
        assert entry["name"] == f"dcm2niix_{key}.zip"
        assert len(entry["sha256"]) == 64
        assert entry["size"] > 0


def test_pinned_tag_yields_a_build_date():
    assert fetch.pinned_build_date() == fetch.pinned_tag().removeprefix("v1.0.")


def test_asset_url_matches_the_release_layout():
    url = fetch.asset_url("lnx")
    assert url.endswith(f"/{fetch.pinned_tag()}/dcm2niix_lnx.zip")
    assert url.startswith("https://github.com/rordenlab/dcm2niix/releases/download/")


def test_unknown_platform_key_is_a_clear_error():
    with pytest.raises(Dcm2niixError, match="no dcm2niix pin recorded"):
        fetch.asset_pin("solaris")


# --- platform detection ------------------------------------------------------


def test_platform_key_is_one_of_the_three():
    assert fetch.platform_key() in {"win", "lnx", "mac"}


def test_binary_name_matches_the_platform():
    assert BINARY == ("dcm2niix.exe" if os.name == "nt" else "dcm2niix")


# --- integrity ---------------------------------------------------------------


def test_verify_asset_accepts_a_matching_payload():
    data = b"payload"
    fetch.verify_asset(data, pin_for(data))


def test_verify_asset_rejects_a_wrong_hash():
    data = b"payload"
    tampered = AssetPin(name="x.zip", sha256="0" * 64, size=len(data))
    with pytest.raises(Dcm2niixError, match="SHA-256"):
        fetch.verify_asset(data, tampered)


def test_verify_asset_rejects_a_wrong_size():
    data = b"payload"
    wrong = AssetPin(name="x.zip", sha256=hashlib.sha256(data).hexdigest(), size=len(data) + 1)
    with pytest.raises(Dcm2niixError, match="bytes but the pin expects"):
        fetch.verify_asset(data, wrong)


def test_size_mismatch_is_checked_before_the_hash():
    """Both are wrong; the size message is the one that names the likely cause."""
    with pytest.raises(Dcm2niixError, match="may have been replaced"):
        fetch.verify_asset(b"short", AssetPin("x.zip", "0" * 64, 9999))


# --- extraction --------------------------------------------------------------


def test_extract_finds_the_binary_at_the_archive_root(tmp_path):
    data = make_zip({BINARY: b"#!/bin/sh\n", "README.txt": b"hi"})
    binary = fetch.extract(data, tmp_path / "out")
    assert binary.name == BINARY
    assert binary.is_file()


def test_extract_finds_the_binary_in_a_subdirectory(tmp_path):
    """The archive layout has varied between releases, so do not assume the root."""
    data = make_zip({f"bin/{BINARY}": b"#!/bin/sh\n"})
    binary = fetch.extract(data, tmp_path / "out")
    assert binary.parent.name == "bin"


def test_extract_sets_the_executable_bit_on_posix(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits do not apply on Windows")
    binary = fetch.extract(make_zip({BINARY: b"#!/bin/sh\n"}), tmp_path / "out")
    assert os.access(binary, os.X_OK)


def test_extract_without_a_binary_is_a_clear_error(tmp_path):
    with pytest.raises(Dcm2niixError, match="no dcm2niix"):
        fetch.extract(make_zip({"README.txt": b"hi"}), tmp_path / "out")


def test_zip_slip_is_rejected(tmp_path):
    """A member escaping the target would let an archive write anywhere on disk."""
    data = make_zip({"../../escaped.txt": b"pwned", BINARY: b"x"})
    with pytest.raises(Dcm2niixError, match="resolves outside"):
        fetch.extract(data, tmp_path / "out")
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_symlink_member_is_rejected(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16  # symlink mode bits
        archive.writestr(info, "/etc/passwd")
        archive.writestr(BINARY, b"x")
    with pytest.raises(Dcm2niixError, match="symlink"):
        fetch.extract(buffer.getvalue(), tmp_path / "out")


# --- resolution order --------------------------------------------------------


def test_explicit_path_wins(tmp_path):
    binary = tmp_path / "my-dcm2niix"
    binary.write_bytes(b"x")
    assert fetch.resolve(explicit=binary) == binary


def test_explicit_path_that_does_not_exist_is_a_clear_error(tmp_path):
    with pytest.raises(Dcm2niixError, match="does not exist"):
        fetch.resolve(explicit=tmp_path / "nope")


def test_env_var_is_honoured(tmp_path, monkeypatch):
    binary = tmp_path / "env-dcm2niix"
    binary.write_bytes(b"x")
    monkeypatch.setenv(fetch.ENV_BINARY, str(binary))
    assert fetch.resolve() == binary


def test_recent_system_binary_is_preferred(tmp_path, monkeypatch):
    system = tmp_path / BINARY
    system.write_bytes(b"x")
    monkeypatch.setattr(fetch.shutil, "which", lambda _: str(system))
    monkeypatch.setattr(fetch, "build_date", lambda _: "29991231")
    assert fetch.resolve() == system


def test_old_system_binary_falls_through_to_the_pinned_build(tmp_path, monkeypatch):
    """A silently-old build producing different orientation would poison the corpus."""
    system = tmp_path / BINARY
    system.write_bytes(b"x")
    monkeypatch.setattr(fetch.shutil, "which", lambda _: str(system))
    monkeypatch.setattr(fetch, "build_date", lambda _: "20200101")

    cached = _seed_cache()
    assert fetch.resolve() == cached


def test_old_system_binary_is_accepted_when_explicitly_allowed(tmp_path, monkeypatch):
    system = tmp_path / BINARY
    system.write_bytes(b"x")
    monkeypatch.setattr(fetch.shutil, "which", lambda _: str(system))
    monkeypatch.setattr(fetch, "build_date", lambda _: "20200101")
    assert fetch.resolve(allow_any_system=True) == system


def test_unparseable_system_version_falls_through(tmp_path, monkeypatch):
    system = tmp_path / BINARY
    system.write_bytes(b"x")
    monkeypatch.setattr(fetch.shutil, "which", lambda _: str(system))
    monkeypatch.setattr(fetch, "build_date", lambda _: None)
    cached = _seed_cache()
    assert fetch.resolve() == cached


def _seed_cache():
    """Put an extracted pinned binary into the isolated cache."""
    target = fetch.dcm2niix_dir(fetch.pinned_tag()) / fetch.platform_key()
    target.mkdir(parents=True, exist_ok=True)
    binary = target / BINARY
    binary.write_bytes(b"x")
    return binary


def test_cached_binary_is_used_without_downloading(monkeypatch):
    cached = _seed_cache()

    def explode(*args, **kwargs):
        raise AssertionError("should not download when the cache is warm")

    monkeypatch.setattr(fetch, "download_asset", explode)
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    assert fetch.resolve() == cached


def test_download_path_verifies_then_extracts(monkeypatch):
    data = make_zip({BINARY: b"#!/bin/sh\n"})
    pin = pin_for(data, fetch.asset_pin().name)
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    monkeypatch.setattr(fetch, "asset_pin", lambda key=None: pin)
    monkeypatch.setattr(fetch, "download_asset", lambda key=None, tag=None: data)

    binary = fetch.resolve()
    assert binary.name == BINARY
    assert binary.is_file()
    # Second call must come from the cache, not the network.
    monkeypatch.setattr(fetch, "download_asset", lambda *a, **k: pytest.fail("re-downloaded"))
    assert fetch.resolve() == binary


def test_tampered_download_is_refused(monkeypatch):
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    monkeypatch.setattr(fetch, "download_asset", lambda key=None, tag=None: b"not the real asset")
    with pytest.raises(Dcm2niixError, match="bytes but the pin expects|SHA-256"):
        fetch.resolve()


# --- offline -----------------------------------------------------------------


def test_offline_with_a_cold_cache_names_the_url_and_the_target(monkeypatch):
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    monkeypatch.setattr(fetch, "download_asset", lambda *a, **k: pytest.fail("downloaded"))
    with pytest.raises(Dcm2niixError) as excinfo:
        fetch.resolve(offline=True)
    message = str(excinfo.value)
    assert "https://github.com/rordenlab/dcm2niix/releases/download/" in message
    assert fetch.asset_pin().sha256 in message
    assert "Extract it into" in message


def test_offline_env_var_is_honoured(monkeypatch):
    monkeypatch.setenv(fetch.ENV_OFFLINE, "1")
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    with pytest.raises(Dcm2niixError, match="downloads are disabled"):
        fetch.resolve()


def test_offline_still_uses_a_warm_cache(monkeypatch):
    cached = _seed_cache()
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    assert fetch.resolve(offline=True) == cached


def test_offline_still_honours_an_explicit_path(tmp_path):
    binary = tmp_path / "explicit"
    binary.write_bytes(b"x")
    assert fetch.resolve(explicit=binary, offline=True) == binary


# --- CLI ---------------------------------------------------------------------


def test_cli_prints_the_path(monkeypatch, capsys):
    cached = _seed_cache()
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    assert fetch.main(["--print-path"]) == 0
    assert capsys.readouterr().out.strip() == str(cached)


def test_cli_returns_nonzero_when_it_cannot_resolve(monkeypatch, capsys):
    monkeypatch.setattr(fetch.shutil, "which", lambda _: None)
    assert fetch.main(["--offline", "--print-path"]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_show_cache(capsys, isolated_cache):
    assert fetch.main(["--show-cache"]) == 0
    assert str(isolated_cache) in capsys.readouterr().out


# --- the real thing ----------------------------------------------------------


@pytest.mark.network
def test_real_download_matches_the_pin_and_runs():
    """Downloads the pinned asset for this platform and runs it."""
    binary = fetch.resolve(prefer_system=False)
    assert fetch.build_date(binary) == fetch.pinned_build_date()
