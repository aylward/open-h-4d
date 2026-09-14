# SPDX-License-Identifier: Apache-2.0
"""Every skill in ``skills/`` must be discoverable by Claude Code.

Claude Code reads project skills from exactly ``<repo>/.claude/skills`` and its
loader drops any entry that is neither a directory nor a symlink. On native
Windows without Developer Mode, git materializes a committed symlink as a plain
text file -- so a skill silently disappears with no error anywhere. That is the
failure this test exists to catch; ``scripts/bootstrap_skills.py`` is the fix.
"""

import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "scripts"))

from bootstrap_skills import discoverable_skills, resolves_to  # noqa: E402

BOOTSTRAP_HINT = "Run: python scripts/bootstrap_skills.py"


def test_at_least_one_skill_exists(repo_root):
    assert discoverable_skills(repo_root / "skills"), (
        "no directory under skills/ contains a SKILL.md -- either the skills are "
        "missing or SKILL.md was renamed"
    )


@pytest.mark.parametrize("name", discoverable_skills())
def test_skill_is_reachable_through_claude_skills(name, repo_root):
    """Each skill has a .claude/skills entry that resolves back to skills/<name>."""
    link = repo_root / ".claude" / "skills" / name
    target = repo_root / "skills" / name

    assert link.exists() or link.is_symlink(), f"missing .claude/skills/{name}. {BOOTSTRAP_HINT}"
    assert not (link.is_file() and not link.is_symlink()), (
        f".claude/skills/{name} is a plain file, not a link -- Claude Code silently "
        f"ignores it. This is the native-Windows symlink failure. {BOOTSTRAP_HINT}"
    )
    assert resolves_to(link, target), (
        f".claude/skills/{name} does not resolve to skills/{name}. {BOOTSTRAP_HINT}"
    )
    assert (link / "SKILL.md").is_file(), f"SKILL.md not readable through .claude/skills/{name}"


def test_no_stale_claude_skills_entries(repo_root):
    """Nothing under .claude/skills without a backing skills/<name>/SKILL.md."""
    claude_skills = repo_root / ".claude" / "skills"
    if not claude_skills.is_dir():
        pytest.skip(".claude/skills does not exist yet")
    valid = set(discoverable_skills(repo_root / "skills"))
    stale = sorted(e.name for e in claude_skills.iterdir() if e.name not in valid)
    assert not stale, f"stale .claude/skills entries: {stale}. {BOOTSTRAP_HINT}"


def test_shared_support_dir_is_not_exposed_as_a_skill(repo_root):
    """skills/openh-4d-shared has no SKILL.md, so it must not be discoverable."""
    shared = repo_root / "skills" / "openh-4d-shared"
    if not shared.is_dir():
        pytest.skip("shared support directory not created yet")
    assert not (shared / "SKILL.md").exists(), (
        "openh-4d-shared must not have a SKILL.md -- it is support material read by "
        "the other skills, not something an agent should invoke"
    )
    assert not (repo_root / ".claude" / "skills" / "openh-4d-shared").exists()
