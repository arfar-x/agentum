"""Who may see which source (spec §6.12, FR-VIS).

The rule is deliberately coarse: a source is either unrestricted or names the
groups allowed to see it, and a caller either is in one of them or is not.
Nothing here knows how a caller was identified -- the MCP server decides that
and hands the use cases a `Caller`.
"""

from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, ConfigDict

MAX_GROUP_LENGTH = 128
#: Bounds on what a caller may name (the sender also applies them). Past these
#: the header is not a list of groups worth believing.
MAX_CALLER_GROUPS = 64
MAX_HEADER_BYTES = 4096


def group_problem(name: object) -> str | None:
    """Why `name` cannot be a group name, or None (FR-VIS-02)."""
    if not isinstance(name, str) or not name.strip():
        return "a group name must not be empty"
    if len(name) > MAX_GROUP_LENGTH:
        return f"group name {name[:20]!r}... is longer than {MAX_GROUP_LENGTH} characters"
    if "," in name:
        return f"group name {name!r} contains a comma"
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        return f"group name {name!r} contains a control character"
    return None


class Caller(BaseModel):
    """The groups a request speaks for.

    Three kinds: anonymous (no group information -- sees unrestricted sources
    only), a member of some groups, and an operator (the CLI, the scheduler and
    the admin console, which see every source: FR-VIS-12).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: frozenset[str] = frozenset()
    operator: bool = False

    @classmethod
    def anonymous(cls) -> "Caller":
        return cls()

    @classmethod
    def operator_tool(cls) -> "Caller":
        return cls(operator=True)

    @classmethod
    def in_groups(cls, groups: Iterable[str]) -> "Caller":
        return cls(groups=frozenset(g for g in groups if group_problem(g) is None))

    @classmethod
    def from_header(cls, value: str | None) -> "Caller":
        """`product,engineering` -> those two groups. Anything unusable is
        dropped, never repaired: a name that does not pass the group rules
        cannot match a source's, so keeping it would only mislead a reader."""
        if not value or len(value.encode("utf-8")) > MAX_HEADER_BYTES:
            return cls.anonymous()
        names = [part.strip() for part in value.split(",")]
        return cls.in_groups(names[:MAX_CALLER_GROUPS])

    def can_see(self, source_groups: Iterable[str]) -> bool:
        """The rule the SQL predicate repeats: unrestricted, or a shared group."""
        required = frozenset(source_groups)
        return self.operator or not required or bool(required & self.groups)
