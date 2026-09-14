# SPDX-License-Identifier: Apache-2.0
"""The OpenH-4D submission directory-naming grammar.

A submission root holds one directory per 4D study, named
``<patient_id><letter>``, plus one auxiliary directory per patient, named
``<patient_id>_ehr``. The letter suffix always starts at ``A``, even when a
patient contributed only one study, so adding a second study later never
renames the first.

Two constraints make the grammar unambiguous, and both are load-bearing:

* **A patient identifier must end in a digit.** Without this, ``STAN-001AB``
  parses two ways (``STAN-001`` + ``AB`` or ``STAN-001A`` + ``B``). With it,
  the split point is provably "after the last digit" and there is exactly one
  parse -- no separator character needed.
* **Underscore is banned inside a patient identifier.** That is what makes the
  ``_ehr`` suffix unambiguous.

Uppercase is mandated so that ``STAN-0001a`` and ``STAN-0001A`` cannot collide
on the case-insensitive filesystems used by Windows and macOS.

The canonical form is ``<PREFIX>-NNNN``, where the steering group issues each
contributor their own prefix (``STAN``, ``DUKE``, ...) so identifiers stay
unique when submissions are merged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The three patterns below are mirrored verbatim in
# skills/openh-4d-shared/layout-spec.md; tests/test_spec_consistency.py asserts
# the two copies stay identical.
_PATIENT_ID_BODY = r"[A-Z][A-Z0-9-]{1,46}[0-9]"

PATIENT_ID_RE = re.compile(rf"^{_PATIENT_ID_BODY}$")
STUDY_DIR_RE = re.compile(rf"^({_PATIENT_ID_BODY})([A-Z]+)$")
EHR_DIR_RE = re.compile(rf"^({_PATIENT_ID_BODY})_ehr$")

EHR_SUFFIX = "_ehr"

#: Kinds returned by :func:`parse_dir_name`.
KIND_STUDY = "study"
KIND_EHR = "ehr"


class InvalidNameError(ValueError):
    """A directory name does not match the OpenH-4D naming grammar."""


@dataclass(frozen=True)
class ParsedName:
    """The result of parsing a submission-level directory name."""

    kind: str
    """Either :data:`KIND_STUDY` or :data:`KIND_EHR`."""

    patient_id: str
    """The patient identifier, without any letter suffix or ``_ehr``."""

    suffix: str | None = None
    """The letter suffix for a study directory; ``None`` for an EHR directory."""

    @property
    def study_index(self) -> int | None:
        """1-based study number (``A`` -> 1), or ``None`` for an EHR directory."""
        return None if self.suffix is None else suffix_to_index(self.suffix)


def is_valid_patient_id(patient_id: str) -> bool:
    """True if ``patient_id`` matches the grammar."""
    return PATIENT_ID_RE.fullmatch(patient_id) is not None


def index_to_suffix(index: int) -> str:
    """Convert a 1-based study number to its letter suffix.

    Bijective base-26, the same scheme spreadsheet columns use, so a patient may
    contribute more than 26 studies:
    ``1 -> "A"``, ``26 -> "Z"``, ``27 -> "AA"``, ``28 -> "AB"``, ``703 -> "AAA"``.
    """
    if index < 1:
        raise ValueError(f"study index must be >= 1, got {index}")
    suffix = ""
    remaining = index
    while remaining > 0:
        remaining, offset = divmod(remaining - 1, 26)
        suffix = chr(ord("A") + offset) + suffix
    return suffix


def suffix_to_index(suffix: str) -> int:
    """Inverse of :func:`index_to_suffix`."""
    if not suffix or not suffix.isascii() or not suffix.isupper() or not suffix.isalpha():
        raise ValueError(f"study suffix must be one or more uppercase letters, got {suffix!r}")
    index = 0
    for char in suffix:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def make_patient_id(prefix: str, number: int, width: int = 4) -> str:
    """Build a canonical patient identifier, e.g. ``("STAN", 1) -> "STAN-0001"``.

    ``prefix`` is the identifier range issued to a contributor by the steering
    group. Passing a prefix or number that would produce an invalid identifier
    raises rather than emitting something the verifier will later reject.
    """
    if number < 0:
        raise ValueError(f"patient number must be >= 0, got {number}")
    patient_id = f"{prefix}-{number:0{width}d}"
    if not is_valid_patient_id(patient_id):
        raise InvalidNameError(
            f"{patient_id!r} is not a valid patient identifier. Prefixes must be uppercase "
            f"letters, digits and hyphens, start with a letter, and the whole identifier "
            f"must be 3-48 characters and end in a digit."
        )
    return patient_id


def study_dir_name(patient_id: str, index: int) -> str:
    """Directory name for the ``index``-th study (1-based) of ``patient_id``."""
    if not is_valid_patient_id(patient_id):
        raise InvalidNameError(f"{patient_id!r} is not a valid patient identifier")
    return f"{patient_id}{index_to_suffix(index)}"


def ehr_dir_name(patient_id: str) -> str:
    """Directory name for the auxiliary/EHR directory of ``patient_id``."""
    if not is_valid_patient_id(patient_id):
        raise InvalidNameError(f"{patient_id!r} is not a valid patient identifier")
    return f"{patient_id}{EHR_SUFFIX}"


def parse_dir_name(name: str) -> ParsedName:
    """Parse a submission-level directory name.

    ``_ehr`` is tested first; because underscore is banned inside a patient
    identifier, a name can never be both an EHR directory and a study directory.

    Raises :class:`InvalidNameError` with a message that names the likely cause,
    since an unparseable directory name is the most common submission defect.
    """
    match = EHR_DIR_RE.fullmatch(name)
    if match:
        return ParsedName(kind=KIND_EHR, patient_id=match.group(1))

    match = STUDY_DIR_RE.fullmatch(name)
    if match:
        return ParsedName(kind=KIND_STUDY, patient_id=match.group(1), suffix=match.group(2))

    raise InvalidNameError(_explain_invalid(name))


def _explain_invalid(name: str) -> str:
    """Best-effort diagnosis of why ``name`` does not parse."""
    if name != name.upper() and (
        STUDY_DIR_RE.fullmatch(name.upper()) or EHR_DIR_RE.fullmatch(name.upper())
    ):
        return (
            f"{name!r} is not uppercase. OpenH-4D directory names must be uppercase so they "
            f"cannot collide on case-insensitive filesystems. Rename to {name.upper()!r}."
        )
    if PATIENT_ID_RE.fullmatch(name):
        return (
            f"{name!r} is a bare patient identifier with no study letter. Every study "
            f"directory carries a letter suffix starting at 'A', even when the patient has "
            f"only one study -- rename to {name}A."
        )
    if name.endswith(EHR_SUFFIX):
        return (
            f"{name!r} ends in '_ehr' but {name[: -len(EHR_SUFFIX)]!r} is not a valid patient "
            f"identifier (3-48 uppercase characters, starting with a letter, ending in a digit, "
            f"no underscores)."
        )
    return (
        f"{name!r} does not match the OpenH-4D naming grammar. Expected "
        f"'<PATIENT_ID><LETTER>' for a study (e.g. 'STAN-0001A') or '<PATIENT_ID>_ehr' for "
        f"auxiliary patient information (e.g. 'STAN-0001_ehr'), where the patient identifier "
        f"is 3-48 uppercase characters, starts with a letter, ends in a digit, and contains "
        f"no underscores."
    )
