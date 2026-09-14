# SPDX-License-Identifier: Apache-2.0
"""The prose spec and the code must not drift apart.

``skills/openh-4d-shared/layout-spec.md`` is what every agent skill reads, and
``openh4d`` is what actually enforces the rules. If they disagree, contributors
are told one thing and graded on another. These tests make the documentation
load-bearing, so drift fails CI instead of surfacing as a confused contributor.
"""

import json
import re

import pytest

from openh4d import naming, schema

SPEC = "skills/openh-4d-shared/layout-spec.md"
SHARED = "skills/openh-4d-shared"


@pytest.fixture
def spec_text(repo_root):
    path = repo_root / SPEC
    assert path.is_file(), f"the normative spec is missing: {SPEC}"
    return path.read_text(encoding="utf-8")


def _grammar_block(spec_text: str) -> dict[str, str]:
    """Extract the ``name ::= pattern`` lines from the grammar code fence."""
    fences = re.findall(r"```\n(.*?)```", spec_text, re.DOTALL)
    for fence in fences:
        if "patient_id ::=" in fence:
            return dict(re.findall(r"^(\w+)\s*::=\s*(\S+)\s*$", fence, re.MULTILINE))
    pytest.fail(f"no grammar code fence found in {SPEC}")


def test_grammar_block_matches_compiled_patterns(spec_text):
    """The three regexes in the spec are the three regexes in openh4d.naming."""
    grammar = _grammar_block(spec_text)
    expected = {
        "patient_id": naming.PATIENT_ID_RE.pattern,
        "study_dir": naming.STUDY_DIR_RE.pattern,
        "ehr_dir": naming.EHR_DIR_RE.pattern,
    }
    assert grammar == expected, (
        "the naming grammar in layout-spec.md has drifted from openh4d.naming. "
        "Update whichever one is wrong -- contributors read the spec."
    )


@pytest.mark.parametrize("template,schema_name", [("patient", "patient"), ("study", "study")])
def test_templates_validate_against_their_schema(repo_root, template, schema_name):
    """A template a contributor is told to copy must itself be valid."""
    path = repo_root / SHARED / f"{template}-json-template.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    validate = {"patient": schema.validate_patient, "study": schema.validate_study}[schema_name]
    findings = validate(document)
    assert findings == [], [f.as_dict() for f in findings]


@pytest.mark.parametrize("template,schema_name", [("patient", "patient"), ("study", "study")])
def test_every_template_key_exists_in_the_schema(repo_root, template, schema_name):
    """A field in the template but not the schema would be silently rejected."""
    document = json.loads(
        (repo_root / SHARED / f"{template}-json-template.json").read_text(encoding="utf-8")
    )
    properties = schema.load_schema(schema_name).get("properties", {})
    unknown = sorted(set(document) - set(properties))
    assert not unknown, f"{template}-json-template.json has keys absent from the schema: {unknown}"


def test_every_conformance_rule_id_is_documented(spec_text):
    """Every rule the verifier can emit appears in the spec's rule table."""
    from openh4d import conformance

    for rule in conformance.RULES:
        assert f"`{rule.rule_id}`" in spec_text, (
            f"conformance rule {rule.rule_id!r} is enforced by code but not documented in {SPEC}"
        )


def test_spec_documents_the_three_study_forms(spec_text):
    for form in ("timepoint_volumes", "dicom_phases", "single_4d"):
        layouts = schema.load_schema("study")["properties"]["file_layout"]["enum"]
        assert form in layouts
    for phrase in ("Form A", "Form B", "Form C"):
        assert phrase in spec_text


def test_spec_states_the_min_timepoints_rule(spec_text):
    """n_timepoints >= 2 is the one rule that can never be waived; say so in both places."""
    assert schema.load_schema("study")["properties"]["n_timepoints"]["minimum"] == 2
    assert "never waivable" in spec_text.lower() or "never be waived" in spec_text.lower()
