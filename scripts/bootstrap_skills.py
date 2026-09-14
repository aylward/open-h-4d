#!/usr/bin/env python3
"""Make the skills in ``skills/`` discoverable by Claude Code on every platform.

``skills/<name>/`` is the canonical, agent-agnostic home for each skill. Claude
Code only looks in ``<repo>/.claude/skills``, so each skill needs an entry there
pointing back at ``../../skills/<name>``.

``.claude/skills/`` is **generated, not committed** -- it is in ``.gitignore``.
That is deliberate. Committing it as symlinks does not survive a native-Windows
clone, where git without Developer Mode writes a plain text file containing the
path, and the Claude Code skill loader silently drops any entry that is neither a
directory nor a symlink. Committing the junctions this script creates instead is
worse: git follows a junction and stores the target's *contents*, so every skill
file would be committed twice and the two copies could drift.

So each clone runs this once. It tries, in order:

    1. already resolves to the right place -- leave it alone
    2. os.symlink()
    3. a Windows directory junction (``mklink /J``), which needs neither admin
       rights nor Developer Mode and which stat() reports as a directory
    4. a plain recursive copy, with a warning that edits belong in ``skills/``

It is idempotent and safe to re-run. Standard library only: it has to work
before the project environment exists, so ``python scripts/bootstrap_skills.py``
is a valid first command in a fresh clone.

Usage:
    python scripts/bootstrap_skills.py [--check]

``--check`` reports what would happen and exits non-zero if anything is wrong,
without modifying the repository.

Exit code 0 = every skill is discoverable, 1 = at least one is not.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
CLAUDE_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"


def discoverable_skills(skills_dir: Path = SKILLS_DIR) -> list[str]:
    """Names of skill directories that carry a SKILL.md, sorted.

    A directory without a SKILL.md (such as ``openh-4d-shared``) is shared
    support material, not an invocable skill, and deliberately gets no entry
    under ``.claude/skills`` so agents do not try to invoke it.
    """
    if not skills_dir.is_dir():
        return []
    return sorted(d.name for d in skills_dir.iterdir() if (d / "SKILL.md").is_file())


def resolves_to(link: Path, target: Path) -> bool:
    """True if ``link`` already points at ``target`` (symlink, junction, or dir)."""
    try:
        return link.exists() and os.path.realpath(link) == os.path.realpath(target)
    except OSError:
        return False


def _remove(path: Path) -> None:
    """Delete a file, symlink, junction, or real directory at ``path``."""
    if not path.exists() and not path.is_symlink():
        return
    # A junction reports as a link but cannot be unlinked; a real dir needs rmtree.
    for attempt in (path.unlink, path.rmdir, lambda: shutil.rmtree(path)):
        try:
            attempt()
            return
        except OSError:
            continue
    raise OSError(f"could not remove {path}")


def _try_symlink(link: Path, name: str) -> bool:
    try:
        os.symlink(Path("..") / ".." / "skills" / name, link, target_is_directory=True)
        return True
    except OSError:
        return False


def _try_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def link_skill(name: str, *, check_only: bool) -> tuple[str, bool]:
    """Ensure ``.claude/skills/<name>`` resolves to ``skills/<name>``.

    Returns ``(rung, ok)`` where ``rung`` names the mechanism used.
    """
    target = SKILLS_DIR / name
    link = CLAUDE_SKILLS_DIR / name

    if resolves_to(link, target):
        return "ok", True
    if check_only:
        return "MISSING", False

    if link.exists() or link.is_symlink():
        _remove(link)

    if _try_symlink(link, name):
        return "symlink", True
    if _try_junction(link, target):
        return "junction", True

    shutil.copytree(target, link)
    return "copy", True


def prune_stale(*, check_only: bool) -> list[str]:
    """Remove ``.claude/skills`` entries with no backing skill. Returns their names."""
    if not CLAUDE_SKILLS_DIR.is_dir():
        return []
    valid = set(discoverable_skills())
    stale = sorted(e.name for e in CLAUDE_SKILLS_DIR.iterdir() if e.name not in valid)
    if not check_only:
        for name in stale:
            _remove(CLAUDE_SKILLS_DIR / name)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report status without modifying anything; exit 1 if any skill is not discoverable",
    )
    args = parser.parse_args(argv)

    skills = discoverable_skills()
    if not skills:
        print(f"No skills with a SKILL.md found under {SKILLS_DIR}")
        return 0

    if not args.check:
        CLAUDE_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    stale = prune_stale(check_only=args.check)
    for name in stale:
        verb = "would remove" if args.check else "removed"
        print(f"  {verb} stale entry .claude/skills/{name}")

    failures = []
    copies = []
    for name in skills:
        try:
            rung, ok = link_skill(name, check_only=args.check)
        except OSError as exc:
            rung, ok = f"FAILED ({exc})", False
        print(f"  {name:<32} {rung}")
        if not ok:
            failures.append(name)
        if rung == "copy":
            copies.append(name)

    if copies:
        print(
            "\nWARNING: copied instead of linked: "
            + ", ".join(copies)
            + "\n  Edit the originals under skills/ and re-run this script; "
            "edits made inside .claude/skills/ will be lost."
        )

    if failures:
        if args.check:
            print(
                "\nThese skills are not discoverable by Claude Code:\n  "
                + "\n  ".join(failures)
                + "\n\nRun: python scripts/bootstrap_skills.py"
            )
        else:
            print("\nCould not surface: " + ", ".join(failures))
        return 1

    print(f"\n{len(skills)} skill(s) discoverable under .claude/skills/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
