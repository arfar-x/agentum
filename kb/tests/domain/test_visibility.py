"""Who may see which source: the pure rule behind FR-VIS."""

from __future__ import annotations

from kb.domain.visibility import Caller, group_problem


def test_group_names_follow_the_rules():
    # Covers: FR-VIS-02
    assert group_problem("product") is None
    assert group_problem("Team A/b") is None
    for bad in ("", "  ", "a,b", "x" * 129, "bell\x07", None):
        assert group_problem(bad), repr(bad)


def test_a_source_is_visible_when_unrestricted_or_a_group_is_shared():
    # Covers: FR-VIS-06
    member = Caller.in_groups(["eng", "ops"])
    assert member.can_see([]) and Caller.anonymous().can_see([])
    assert member.can_see(["product", "eng"])
    assert not member.can_see(["product"])
    assert not Caller.anonymous().can_see(["eng"])


def test_operator_tools_see_every_source():
    # Covers: FR-VIS-12
    assert Caller.operator_tool().can_see(["anything"])


def test_the_header_is_cleaned_not_trusted():
    # Covers: FR-VIS-10
    assert Caller.from_header("eng,product").groups == {"eng", "product"}
    assert Caller.from_header(" eng , ,eng,bad\x07").groups == {"eng"}
    assert Caller.from_header(None).groups == frozenset()
    assert Caller.from_header("x" * 5000).groups == frozenset(), "oversized: no groups"
    many = ",".join(f"g{n}" for n in range(100))
    assert len(Caller.from_header(many).groups) == 64
