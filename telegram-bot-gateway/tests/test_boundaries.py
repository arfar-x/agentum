"""The hexagonal boundary, enforced: domain and application do no I/O."""

from __future__ import annotations

import ast
from pathlib import Path

# Covers: NFR-ARC-01, NFR-ARC-02

SRC = Path(__file__).resolve().parent.parent / "src" / "telegram_bot_gateway"

FORBIDDEN_PREFIXES = (
    "httpx", "telegram", "psycopg", "psycopg_pool", "requests",
    "yaml", "psycopg", "redis", "requests", "cryptography", "openai", "telegram_bot_gateway.adapters",
)
FORBIDDEN_STDLIB = {"socket", "urllib", "sqlite3", "http", "subprocess", "asyncio"}


def _imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def _violations(layer: str, extra_forbidden: tuple[str, ...] = ()) -> list[str]:
    problems: list[str] = []
    for path in sorted((SRC / layer).rglob("*.py")):
        for lineno, module in _imports(path):
            root = module.split(".")[0]
            if root in FORBIDDEN_STDLIB or any(
                module == p or module.startswith(p + ".") for p in FORBIDDEN_PREFIXES + extra_forbidden
            ):
                problems.append(f"{path.relative_to(SRC)}:{lineno} imports {module}")
    return problems


def test_the_domain_layer_does_no_io():
    assert not _violations("domain", ("telegram_bot_gateway.application",))


def test_the_application_layer_does_no_io():
    assert not _violations("application")
