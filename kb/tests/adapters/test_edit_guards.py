"""What the console may and may not write into the source configuration.

The scheduler reads this file with its whole environment (the stack's `.env`) and
sends a source's credential to the URL the file names. The console can edit the
file, so these are the lines it must not be able to cross (FR-CFG-14, FR-CFG-15).
"""

from __future__ import annotations

import pytest

from kb.application.ports.source_files import CreateSource, UpdateSource
from kb.application.use_cases.edit_sources import InvalidChange
from tests.adapters.test_edit_sources import levels, texts, world  # noqa: F401 - fixture

GITLAB = {
    "id": "adrs",
    "kind": "gitlab",
    "enabled": False,
    "base_url": "https://gitlab.example.com",
    "token_env": "GITLAB_TOKEN",
    "projects": [{"path": "team/adrs", "scopes": [{"dir": ".", "type": "spec"}]}],
}


def create(config):
    return CreateSource(config=config)


@pytest.mark.parametrize(
    "name",
    ["KB_ADMIN_TOKEN", "JWT_SECRET", "CREDS_KEY", "KB_POSTGRES_PASSWORD", "MEILI_MASTER_KEY",
     "KB_ADMIN_DB_PASSWORD", "ADMIN_PANEL_SESSION_TOKEN", "KB_SUMMARIZER_API_KEY", "lowercase_token", "TOKEN_AND_MORE"],
)
def test_a_source_cannot_be_given_another_components_secret_as_its_credential(world, name):
    # Covers: FR-CFG-14
    editor, file, _ = world
    with pytest.raises(InvalidChange, match="cannot be used from the console"):
        editor.preview(create({**GITLAB, "token_env": name}))
    assert file.writes == 0


@pytest.mark.parametrize("name", ["GITLAB_TOKEN", "KB_GITLAB_TOKEN", "TEAM_GITLAB_PAT", "TOKEN"])
def test_an_ordinary_credential_variable_is_accepted(world, name):
    # Covers: FR-CFG-14
    editor, _, _ = world
    assert editor.preview(create({**GITLAB, "token_env": name})).changed


@pytest.mark.parametrize("secret", ["JWT_SECRET", "KB_ADMIN_TOKEN", "CREDS_KEY", "KB_POSTGRES_PASSWORD"])
def test_a_placeholder_cannot_smuggle_a_secret_into_a_url(world, secret):
    # Covers: FR-CFG-14
    editor, _, _ = world
    with pytest.raises(InvalidChange, match="cannot be written from the console"):
        editor.preview(create({**GITLAB, "base_url": f"https://evil.example/${{{secret}}}"}))
    with pytest.raises(InvalidChange, match="cannot be written from the console"):
        editor.preview(UpdateSource(id="jira-product", config={"jql": f"${{{secret}:-x}}"}))


def test_a_placeholder_for_a_location_is_fine(world):
    # Covers: FR-CFG-14
    editor, _, _ = world
    preview = editor.preview(create({**GITLAB, "base_url": "${GITLAB_BASE_URL:-https://gitlab.example.com}"}))
    assert preview.changed and "${GITLAB_BASE_URL:-https://gitlab.example.com}" in preview.diff


def test_what_the_file_already_says_is_not_judged_again(world):
    # Covers: FR-CFG-14
    editor, file, _ = world
    file.text = file.text + (
        "  - id: legacy\n    kind: gitlab\n    enabled: false\n    base_url: ${GL_INSTANCE}\n"
        "    token_env: GITLAB_API_KEY\n    projects:\n      - path: a/b\n        scopes: [{dir: '.', type: spec}]\n"
    )
    # An unrelated edit that restates the values the file has: allowed.
    preview = editor.preview(UpdateSource(id="legacy", config={
        "enabled": True, "base_url": "${GL_INSTANCE}", "token_env": "GITLAB_API_KEY"}))
    assert preview.changed
    # But moving the credential somewhere new is judged.
    with pytest.raises(InvalidChange):
        editor.preview(UpdateSource(id="legacy", config={"token_env": "JWT_REFRESH_SECRET"}))


def test_the_approval_says_which_credential_goes_to_which_server(world):
    # Covers: FR-CFG-14
    editor, _, _ = world
    preview = editor.preview(create(GITLAB))
    assert "GITLAB_TOKEN" in texts(preview) and "https://gitlab.example.com" in texts(preview)
    assert "warning" in levels(preview)


@pytest.mark.parametrize("clash", ["Confluence-Eng", "CONFLUENCE-ENG", "confluence-ENG"])
def test_ids_that_differ_only_by_case_are_the_same_id(world, clash):
    # Covers: FR-CFG-15
    editor, _, _ = world
    with pytest.raises(InvalidChange, match="already a source"):
        editor.preview(create({"id": clash, "kind": "confluence", "spaces": ["X"]}))


@pytest.mark.parametrize("reserved", ["form", "Form", "FORM"])
def test_the_manual_records_id_is_reserved_in_any_case(world, reserved):
    # Covers: FR-CFG-15
    editor, _, _ = world
    with pytest.raises(InvalidChange):
        editor.preview(create({"id": reserved, "kind": "jira", "jql": "project = X"}))


def test_a_file_that_already_has_such_a_clash_is_refused_whole():
    # Covers: FR-CFG-15
    from kb.sources_config import ConfigError, parse

    text = "sources:\n  - {id: Docs, kind: jira, jql: a}\n  - {id: docs, kind: jira, jql: b}\n"
    with pytest.raises(ConfigError, match="same id"):
        parse(text, env={})


def test_a_file_that_is_not_utf8_is_a_configuration_error_not_a_crash(tmp_path):
    # Covers: FR-SCH-12
    from kb.sources_config import ConfigError, load

    path = tmp_path / "kb-sources.yaml"
    path.write_bytes(b"sources: [\xff\xfe")
    with pytest.raises(ConfigError, match="UTF-8"):
        load(path)
