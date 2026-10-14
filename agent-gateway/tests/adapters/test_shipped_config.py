"""The configuration that ships in config/ must load: it is what the first run uses."""

from pathlib import Path

from agent_gateway.adapters.outbound.config_files import load_config

CONFIG = Path(__file__).resolve().parents[2] / "config"


def test_the_shipped_agents_load_with_valid_handoffs_and_tool_servers():
    cfg = load_config(CONFIG, {"DEFAULT_MODEL": "m"})
    assert {"product-assistant", "plan", "doc-gen", "prepare-for-dev", "engineering-review", "tasks", "docs-manager"} <= set(cfg.agents)
    router = cfg.agents["product-assistant"]
    assert {h.to for h in router.handoffs} >= {"prepare-for-dev", "plan", "tasks"}
    assert cfg.agents["tasks"].preretrieval.kb.enabled is False
    assert cfg.agents["prepare-for-dev"].preretrieval.kb.enabled is True


def test_every_write_tool_the_agents_use_is_behind_approval():
    cfg = load_config(CONFIG, {"DEFAULT_MODEL": "m"})
    writes = ("create", "update", "patch", "delete", "add_", "remove_", "edit", "transition", "worklog",
              "upload")
    for agent in cfg.agents.values():
        for binding in agent.tools:
            if binding.server != "agent-skills":
                continue
            for tool in binding.include:
                if any(w in tool for w in writes) and tool not in {"jira_worklog_report", "jira_worklog"} and "get_" not in tool:
                    assert tool in binding.approval, f"{agent.id}: {tool} writes but is not approval-gated"


def test_no_literal_model_or_secret_is_in_the_shipped_config():
    for path in CONFIG.rglob("*.yaml"):
        text = path.read_text().lower()
        assert "password:" not in text and "api_key" not in text, path
    assert "${DEFAULT_MODEL}" in (CONFIG / "agents" / "plan.yaml").read_text()
