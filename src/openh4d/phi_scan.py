# SPDX-License-Identifier: Apache-2.0
"""Scan for protected health information.

**OpenH-4D tooling verifies de-identification; it does not perform it.** RFP
section 10 puts the obligation on the proposer, and writing an anonymizer is a
large, high-liability piece of work when mature implementations already exist
(RSNA CTP, DICOM PS3.15 Basic Application Level Confidentiality Profile). What
this module does is refuse to let obviously-identified data through, and audit a
finished submission.

Two entry points:

* :func:`scan_dicom_source` runs before organizing. A hit is a hard stop with a
  message telling the contributor to de-identify first.
* :func:`scan_submission` runs over a finished submission: retained DICOM tags,
  every JSON sidecar, free text under ``reports/``, the data card, and filenames.
  Any hit is a blocker in the submission evaluation.

Usage::

    python -m openh4d.phi_scan <directory>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: DICOM keywords that must be absent or empty in a de-identified study. Each is
#: one of the 18 HIPAA Safe Harbor identifiers, or a direct route to one.
PHI_DICOM_KEYWORDS = (
    "PatientName",
    "PatientBirthDate",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "PatientMotherBirthName",
    "OtherPatientIDs",
    "OtherPatientNames",
    "OtherPatientIDsSequence",
    "AccessionNumber",
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "ReferringPhysicianName",
    "ReferringPhysicianTelephoneNumbers",
    "PerformingPhysicianName",
    "NameOfPhysiciansReadingStudy",
    "OperatorsName",
    "RequestingPhysician",
    "IssuerOfPatientID",
    "MedicalRecordLocator",
    "InsurancePlanIdentification",
    "PatientInsurancePlanCodeSequence",
    "MilitaryRank",
    "DeviceSerialNumber",
)

#: Date tags. A full date is itself a Safe Harbor identifier; a bare year is not.
PHI_DATE_KEYWORDS = (
    "StudyDate",
    "SeriesDate",
    "AcquisitionDate",
    "ContentDate",
    "PatientBirthDate",
    "AcquisitionDateTime",
)

#: Values that mean "already de-identified", not "here is a name".
PSEUDONYM_RE = re.compile(
    r"^(anon\w*|anonymi[sz]ed|deident\w*|removed|redacted|none|null|unknown|n/?a|test|"
    r"[A-Z]{2,8}-\d{2,}[A-Z]*|\d+)$",
    re.IGNORECASE,
)

DATE_RE = re.compile(r"\b(19|20)\d{2}[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b")
DICOM_DATE_RE = re.compile(r"^(19|20)\d{6}$")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b(?:\+?1[-. ])?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
MRN_RE = re.compile(r"\b(?:mrn|medical\s+record\s+(?:number|no\.?))\s*[:#]?\s*\S+", re.IGNORECASE)

#: Ages of 90 and over are a Safe Harbor identifier and must be collapsed.
AGE_RE = re.compile(r"\b(9\d|1\d{2})\s*(?:y(?:ears?)?[- ]?o(?:ld)?|yo|yrs?)\b", re.IGNORECASE)

#: Text files worth reading in a submission sweep.
TEXT_SUFFIXES = {".json", ".md", ".txt", ".csv", ".yaml", ".yml"}

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"


@dataclass
class PhiFinding:
    """One suspected identifier, located precisely enough to act on."""

    code: str
    message: str
    path: str = ""
    severity: str = SEVERITY_BLOCKER
    evidence: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.path:
            out["path"] = self.path
        if self.evidence:
            out["evidence"] = self.evidence
        return out


@dataclass
class PhiReport:
    target: Path
    findings: list[PhiFinding] = field(default_factory=list)
    n_files_scanned: int = 0

    @property
    def clean(self) -> bool:
        return not any(f.severity == SEVERITY_BLOCKER for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": str(self.target),
            "clean": self.clean,
            "n_files_scanned": self.n_files_scanned,
            "n_findings": len(self.findings),
            "findings": [f.as_dict() for f in self.findings],
        }


def _redact(value: str, keep: int = 3) -> str:
    """Show enough of a hit to locate it, without copying PHI into a report."""
    text = str(value)
    if len(text) <= keep:
        return "*" * len(text)
    return text[:keep] + "*" * min(len(text) - keep, 12)


def looks_like_a_pseudonym(value: Any) -> bool:
    """True if ``value`` is a placeholder rather than a real identifier.

    DICOM PersonName values are caret-separated components (``ANON^ANON``,
    ``^^^^``), so each component is tested independently: a value is a pseudonym
    when every non-empty component is one.
    """
    text = str(value).strip()
    if not text:
        return True
    components = [part.strip() for part in text.split("^")]
    populated = [part for part in components if part]
    if not populated:
        return True
    return all(PSEUDONYM_RE.match(part) for part in populated)


# --- free text ----------------------------------------------------------------


def _normalize_separators(text: str) -> str:
    """Turn underscores into spaces before matching.

    The patterns below are anchored on word boundaries, and ```` does not
    match between an underscore and a digit -- so ``scan_1962-04-17.txt`` would
    slip past a date check that ``scan 1962-04-17.txt`` catches. An underscore is
    never part of a date, a phone number or a social security number, so
    treating it as a separator only ever adds detections.
    """
    return text.replace("_", " ")


def scan_text(text: str, path: str = "") -> list[PhiFinding]:
    """Look for the identifier shapes that survive a sloppy de-identification."""
    text = _normalize_separators(text)
    findings = []
    patterns = (
        ("E_PHI_DATE", DATE_RE, "a calendar date"),
        ("E_PHI_SSN", SSN_RE, "a social security number"),
        ("E_PHI_PHONE", PHONE_RE, "a telephone number"),
        ("E_PHI_EMAIL", EMAIL_RE, "an email address"),
        ("E_PHI_MRN", MRN_RE, "a medical record number"),
        ("E_PHI_AGE_OVER_89", AGE_RE, "an age of 90 or over"),
    )
    for code, pattern, description in patterns:
        match = pattern.search(text)
        if match:
            findings.append(
                PhiFinding(
                    code=code,
                    message=(
                        f"found what looks like {description}. This is one of the 18 HIPAA "
                        f"Safe Harbor identifiers and must be removed or generalized."
                    ),
                    path=path,
                    evidence=_redact(match.group(0)),
                )
            )
    return findings


# --- DICOM --------------------------------------------------------------------


def scan_dicom_file(path: Path, relative: str = "") -> list[PhiFinding]:
    """Check one DICOM header against the Safe Harbor identifier list."""
    import pydicom
    from pydicom.errors import InvalidDicomError

    location = relative or str(path)
    try:
        dataset = pydicom.dcmread(str(path), stop_before_pixels=True)
    except (InvalidDicomError, OSError):
        return []
    except Exception:  # noqa: BLE001
        return []

    findings: list[PhiFinding] = []

    for keyword in PHI_DICOM_KEYWORDS:
        value = dataset.get(keyword)
        if value in (None, ""):
            continue
        if looks_like_a_pseudonym(value):
            continue
        findings.append(
            PhiFinding(
                code="E_PHI_DICOM_TAG",
                message=(
                    f"{keyword} is populated. It is a HIPAA Safe Harbor identifier and must be "
                    f"removed before the data is submitted."
                ),
                path=location,
                evidence=f"{keyword}={_redact(value)}",
            )
        )

    for keyword in PHI_DATE_KEYWORDS:
        value = dataset.get(keyword)
        if value and DICOM_DATE_RE.match(str(value).strip()):
            findings.append(
                PhiFinding(
                    code="E_PHI_DICOM_DATE",
                    message=(
                        f"{keyword} carries a full date. Dates more precise than a year are "
                        f"Safe Harbor identifiers -- apply a consistent date shift, or remove "
                        f"the tag."
                    ),
                    path=location,
                    evidence=f"{keyword}={_redact(value, keep=4)}",
                )
            )

    if str(dataset.get("BurnedInAnnotation", "")).upper() == "YES":
        findings.append(
            PhiFinding(
                code="E_PHI_BURNED_IN",
                message=(
                    "BurnedInAnnotation is YES, so identifying text may be rendered into the "
                    "pixel data itself. Crop or redact the annotated region; removing tags does "
                    "not help here."
                ),
                path=location,
            )
        )

    private_blocks = sorted({element.tag.group for element in dataset if element.tag.is_private})
    if private_blocks:
        findings.append(
            PhiFinding(
                code="W_PHI_PRIVATE_TAGS",
                severity=SEVERITY_WARNING,
                message=(
                    f"{len(private_blocks)} private tag group(s) present "
                    f"({', '.join(hex(g) for g in private_blocks[:6])}). Vendors store arbitrary "
                    f"data there, including identifiers. OpenH-4D drops every private tag when "
                    f"it retains DICOM."
                ),
                path=location,
            )
        )

    return findings


def scan_dicom_source(source: Path, *, max_files: int | None = 500) -> PhiReport:
    """Pre-flight a DICOM source before organizing it.

    Sampling: PHI lives in the header, and a contributor's export is
    overwhelmingly uniform, so reading a bounded sample catches a
    not-de-identified source without reading 100,000 files. Pass
    ``max_files=None`` for an exhaustive audit.
    """
    source = Path(source)
    report = PhiReport(target=source)
    seen_codes: set[tuple[str, str]] = set()

    candidates = [p for p in sorted(source.rglob("*")) if p.is_file()]
    if max_files is not None and len(candidates) > max_files:
        step = len(candidates) // max_files + 1
        candidates = candidates[::step]

    for path in candidates:
        report.n_files_scanned += 1
        relative = path.relative_to(source).as_posix()
        for finding in scan_dicom_file(path, relative):
            # One example per distinct problem: a 100,000-file export would
            # otherwise produce 100,000 identical findings.
            key = (finding.code, finding.evidence.split("=")[0])
            if key in seen_codes:
                continue
            seen_codes.add(key)
            report.findings.append(finding)

    return report


# --- submission ---------------------------------------------------------------


def _walk_json(document: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Every string leaf in a JSON document, with its dotted path."""
    out: list[tuple[str, str]] = []
    if isinstance(document, dict):
        for key, value in document.items():
            out += _walk_json(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            out += _walk_json(value, f"{prefix}[{index}]")
    elif isinstance(document, str):
        out.append((prefix, document))
    return out


#: Sidecar fields where a date is expected and therefore allowed.
DATE_ALLOWED_PATH_PREFIXES = ("deidentification", "generated_at", "source.converted_at")


def scan_submission(root: Path, *, scan_dicom: bool = True) -> PhiReport:
    """Audit a finished submission. Any blocker here fails the evaluation."""
    root = Path(root)
    report = PhiReport(target=root)

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        suffix = "".join(path.suffixes[-1:]).lower()

        if suffix == ".json":
            report.n_files_scanned += 1
            report.findings += _scan_json_file(path, relative)
        elif suffix in TEXT_SUFFIXES:
            report.n_files_scanned += 1
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            report.findings += scan_text(text, relative)
        elif scan_dicom and suffix == ".dcm":
            report.n_files_scanned += 1
            report.findings += scan_dicom_file(path, relative)

        report.findings += _scan_filename(path.name, relative)

    return report


def _scan_json_file(path: Path, relative: str) -> list[PhiFinding]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    findings = []
    for json_path, value in _walk_json(document):
        if any(json_path.startswith(allowed) for allowed in DATE_ALLOWED_PATH_PREFIXES):
            continue
        findings += [
            PhiFinding(
                code=finding.code,
                message=finding.message,
                path=f"{relative}:{json_path}",
                severity=finding.severity,
                evidence=finding.evidence,
            )
            for finding in scan_text(value)
        ]
    return findings


def _scan_filename(name: str, relative: str) -> list[PhiFinding]:
    """A filename can carry a name or a date just as easily as a field can."""
    findings = []
    normalized = _normalize_separators(name)
    if DATE_RE.search(normalized) or SSN_RE.search(normalized):
        findings.append(
            PhiFinding(
                code="E_PHI_FILENAME",
                message=(
                    "the filename itself contains what looks like a date or identifier. "
                    "Rename it -- filenames travel with the data."
                ),
                path=relative,
                evidence=_redact(name, keep=6),
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="a DICOM source, or a submission root")
    parser.add_argument(
        "--mode",
        choices=["source", "submission"],
        default="source",
        help="'source' pre-flights a DICOM export; 'submission' audits a finished tree",
    )
    parser.add_argument(
        "--exhaustive",
        action="store_true",
        help="read every file rather than a bounded sample (source mode)",
    )
    args = parser.parse_args(argv)

    if not args.target.exists():
        print(f"no such path: {args.target}", file=sys.stderr)
        return 1

    if args.mode == "source":
        report = scan_dicom_source(args.target, max_files=None if args.exhaustive else 500)
    else:
        report = scan_submission(args.target)

    print(json.dumps(report.as_dict(), indent=2))
    if not report.clean:
        print(
            "\nThis data is not de-identified. OpenH-4D tooling will not de-identify it for "
            "you -- run a DICOM PS3.15 confidentiality profile implementation (RSNA CTP or "
            "equivalent) first, then re-run this check.",
            file=sys.stderr,
        )
    return 0 if report.clean else 1


if __name__ == "__main__":
    sys.exit(main())
