# SPDX-License-Identifier: Apache-2.0
"""The submission directory-naming grammar.

The case that matters most is the letter-suffix disambiguation: a patient
identifier ending in a digit is what makes ``STAN-001AB`` parse exactly one way.
"""

import pytest

from openh4d.naming import (
    KIND_EHR,
    KIND_STUDY,
    InvalidNameError,
    ehr_dir_name,
    index_to_suffix,
    is_valid_patient_id,
    make_patient_id,
    parse_dir_name,
    study_dir_name,
    suffix_to_index,
)

VALID_PATIENT_IDS = [
    "STAN-0001",
    "DUKE-0001",
    "OH4D-0001",
    "AB1",  # minimum length
    "A" + "B" * 46 + "9",  # maximum length, 48 chars
    "X-1-2-3",
    "CHOP0042",
]

INVALID_PATIENT_IDS = [
    ("", "empty"),
    ("AB", "too short"),
    ("A" + "B" * 47 + "9", "49 chars, too long"),
    ("stan-0001", "lowercase"),
    ("STAN-0001A", "ends in a letter, so the suffix split would be ambiguous"),
    ("1STAN-0001", "must start with a letter"),
    ("STAN_0001", "underscore would collide with the _ehr suffix"),
    ("STAN 0001", "space"),
    ("STAN.0001", "dot"),
]


@pytest.mark.parametrize("patient_id", VALID_PATIENT_IDS)
def test_valid_patient_ids(patient_id):
    assert is_valid_patient_id(patient_id)


@pytest.mark.parametrize("patient_id,why", INVALID_PATIENT_IDS)
def test_invalid_patient_ids(patient_id, why):
    assert not is_valid_patient_id(patient_id), f"{patient_id!r} should be rejected: {why}"


# --- bijective base-26 -------------------------------------------------------


@pytest.mark.parametrize(
    "index,suffix",
    [
        (1, "A"),
        (2, "B"),
        (26, "Z"),
        (27, "AA"),
        (28, "AB"),
        (52, "AZ"),
        (53, "BA"),
        (702, "ZZ"),
        (703, "AAA"),
    ],
)
def test_index_to_suffix(index, suffix):
    assert index_to_suffix(index) == suffix
    assert suffix_to_index(suffix) == index


def test_suffix_round_trip_is_total():
    """Every study index from 1 to 1000 round-trips, so >26 studies is not a cliff."""
    for index in range(1, 1001):
        assert suffix_to_index(index_to_suffix(index)) == index


def test_index_to_suffix_rejects_zero_and_negative():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            index_to_suffix(bad)


@pytest.mark.parametrize("bad", ["", "a", "A1", "AB-", " A"])
def test_suffix_to_index_rejects_non_letters(bad):
    with pytest.raises(ValueError):
        suffix_to_index(bad)


# --- parsing -----------------------------------------------------------------


def test_parse_study_dir():
    parsed = parse_dir_name("STAN-0001A")
    assert parsed.kind == KIND_STUDY
    assert parsed.patient_id == "STAN-0001"
    assert parsed.suffix == "A"
    assert parsed.study_index == 1


def test_parse_ehr_dir():
    parsed = parse_dir_name("STAN-0001_ehr")
    assert parsed.kind == KIND_EHR
    assert parsed.patient_id == "STAN-0001"
    assert parsed.suffix is None
    assert parsed.study_index is None


@pytest.mark.parametrize(
    "name,patient_id,suffix",
    [
        # The disambiguation hazard: the split is always after the last digit.
        ("STAN-001AB", "STAN-001", "AB"),
        ("STAN-0001Z", "STAN-0001", "Z"),
        ("A1B2C", "A1B2", "C"),
        ("AB1A", "AB1", "A"),
        # A hyphen inside the identifier does not confuse the split.
        ("X-1-2-3AA", "X-1-2-3", "AA"),
    ],
)
def test_suffix_split_is_unambiguous(name, patient_id, suffix):
    parsed = parse_dir_name(name)
    assert (parsed.patient_id, parsed.suffix) == (patient_id, suffix)


@pytest.mark.parametrize(
    "name",
    ["STAN-0001", "stan-0001a", "STAN-0001-A", "STAN-0001_EHR", "STAN-0001_ehr_extra", "README.md"],
)
def test_parse_rejects_invalid_names(name):
    with pytest.raises(InvalidNameError):
        parse_dir_name(name)


def test_bare_patient_id_error_names_the_fix():
    """The commonest mistake is omitting the letter on a single-study patient."""
    with pytest.raises(InvalidNameError, match="STAN-0001A"):
        parse_dir_name("STAN-0001")


def test_lowercase_error_names_the_fix():
    with pytest.raises(InvalidNameError, match="uppercase"):
        parse_dir_name("stan-0001a")


# --- construction ------------------------------------------------------------


def test_make_patient_id():
    assert make_patient_id("STAN", 1) == "STAN-0001"
    assert make_patient_id("DUKE", 1234) == "DUKE-1234"
    assert make_patient_id("OH4D", 7, width=6) == "OH4D-000007"


@pytest.mark.parametrize("prefix", ["stan", "1STAN", "STAN_X", "S T"])
def test_make_patient_id_rejects_bad_prefix(prefix):
    with pytest.raises(InvalidNameError):
        make_patient_id(prefix, 1)


def test_study_and_ehr_dir_names():
    assert study_dir_name("STAN-0001", 1) == "STAN-0001A"
    assert study_dir_name("STAN-0001", 27) == "STAN-0001AA"
    assert ehr_dir_name("STAN-0001") == "STAN-0001_ehr"


def test_constructed_names_round_trip():
    """Anything the tooling builds must parse back to what it was built from."""
    patient_id = make_patient_id("STAN", 42)
    for index in (1, 2, 26, 27, 100):
        parsed = parse_dir_name(study_dir_name(patient_id, index))
        assert parsed.patient_id == patient_id
        assert parsed.study_index == index
    assert parse_dir_name(ehr_dir_name(patient_id)).patient_id == patient_id


def test_dir_name_builders_reject_invalid_patient_id():
    for builder in (lambda: study_dir_name("stan-0001", 1), lambda: ehr_dir_name("stan-0001")):
        with pytest.raises(InvalidNameError):
            builder()
