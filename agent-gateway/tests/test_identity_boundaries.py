# Covers: FR-IDP-01, FR-IDP-05, FR-IDP-07
"""The identity provider stays behind its ports: only the composition root and tests know Keycloak."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "agent_gateway"
ADAPTER = "adapters/outbound/identity/keycloak"
ALLOWED_IMPORTERS = {"container.py"}


def test_nothing_but_the_composition_root_imports_the_keycloak_adapter():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith(ADAPTER) or rel in ALLOWED_IMPORTERS:
            continue
        if "identity.keycloak" in path.read_text(encoding="utf-8") or "identity import keycloak" in path.read_text():
            offenders.append(rel)
    assert not offenders, offenders


def test_domain_and_application_name_no_provider_and_read_no_provider_setting():
    pattern = re.compile(r"keycloak|OIDC_|DIRECTORY_CLIENT|PORTAL_CLIENT|realm", re.IGNORECASE)
    offenders = []
    for layer in ("domain", "application"):
        for path in (SRC / layer).rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    assert not offenders, offenders


def test_a_provider_is_one_package_with_the_three_ports():
    package = SRC / ADAPTER
    assert {p.name for p in package.glob("*.py")} >= {"verifier.py", "directory.py", "portal_login.py", "settings.py", "__init__.py"}
