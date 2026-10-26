"""`groups:` in sources.yaml (spec §6.12)."""

from __future__ import annotations

import pytest

from kb.sources_config import ConfigError, load


def write(tmp_path, text: str):
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_source_may_name_groups_and_none_means_unrestricted(tmp_path):
    # Covers: FR-VIS-01
    config = load(write(tmp_path, """
sources:
  - { id: open, kind: confluence, enabled: true, spaces: [A] }
  - { id: closed, kind: confluence, enabled: true, spaces: [B], groups: [eng, product] }
  - { id: empty, kind: confluence, enabled: true, spaces: [C], groups: [] }
"""))
    assert config.visibility() == {"open": (), "closed": ("eng", "product"), "empty": ()}


@pytest.mark.parametrize(
    "groups",
    ['[""]', '["a,b"]', '["' + "x" * 129 + '"]', "[eng, eng]", '["tab\\there"]'],
)
def test_unusable_group_names_are_refused(tmp_path, groups):
    # Covers: FR-VIS-02
    with pytest.raises(ConfigError, match="group"):
        load(write(tmp_path, f"""
sources:
  - {{ id: closed, kind: confluence, spaces: [B], groups: {groups} }}
"""))


def test_groups_are_refused_under_defaults(tmp_path):
    # Covers: FR-VIS-02
    with pytest.raises(ConfigError, match="defaults.groups"):
        load(write(tmp_path, """
defaults:
  groups: [eng]
sources: []
"""))
