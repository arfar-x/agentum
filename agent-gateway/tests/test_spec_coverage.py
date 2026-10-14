"""The spec is executable: this test is what makes it so.

`docs/spec.md` defines every requirement with a stable id and a status. A test claims one with a
`Covers: FR-RTE-01, FR-RTE-02` line. This test fails when a requirement marked *done* has no
test claiming it, when a test claims an id the spec does not define, or when an unbuilt
requirement already has tests (its status is stale).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "spec.md"
TESTS = Path(__file__).resolve().parent

REQUIREMENT_ROW = re.compile(
    r"^\|\s*((?:FR|NFR|AS)-[A-Z]*-?\d{2})\s*\|.*\|\s*(done|partial|planned|deferred)\s*\|\s*$",
    re.MULTILINE,
)
CLAIM = re.compile(r"Covers:\s*([A-Z][A-Z-]*-\d{2}(?:\s*,\s*[A-Z][A-Z-]*-\d{2})*)")


def spec_requirements() -> dict[str, str]:
    text = SPEC.read_text(encoding="utf-8")
    return {m.group(1): m.group(2) for m in REQUIREMENT_ROW.finditer(text)}


def claims() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for match in CLAIM.finditer(path.read_text(encoding="utf-8")):
            for rid in (part.strip() for part in match.group(1).split(",")):
                found.setdefault(rid, set()).add(path.name)
    return found


def test_the_spec_defines_requirements_this_test_can_read():
    requirements = spec_requirements()
    assert len(requirements) > 60, "spec parsed into too few requirements -- has the table format changed?"
    assert "FR-RTE-01" in requirements


def test_every_implemented_requirement_has_a_test_claiming_it():
    implemented = {rid for rid, status in spec_requirements().items() if status == "done"}
    unclaimed = sorted(implemented - set(claims()))
    assert not unclaimed, "marked done in docs/spec.md but no test claims them: " + ", ".join(unclaimed)


def test_no_test_claims_a_requirement_the_spec_does_not_define():
    defined = set(spec_requirements())
    unknown = sorted(
        f"{rid} (claimed by {', '.join(sorted(files))})"
        for rid, files in claims().items()
        if rid not in defined
    )
    assert not unknown, "claimed by tests but not defined in the spec: " + "; ".join(unknown)


def test_unbuilt_requirements_are_not_quietly_implemented():
    unbuilt = {rid for rid, status in spec_requirements().items() if status in {"planned", "deferred"}}
    stale = sorted(unbuilt & set(claims()))
    assert not stale, "tests claim requirements the spec still marks planned/deferred: " + ", ".join(stale)
