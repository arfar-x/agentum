"""Who may use which agent: the decision table of the identity spec, as pure functions.

Access is evaluated for every request from the caller's principal and is never stored in a thread
or a paused turn, so a change of membership takes effect on the next request.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_gateway.domain.agent import AgentDefinition
from agent_gateway.domain.identity import Principal


def _matches(principal: Principal, groups: tuple[str, ...] | None, roles: tuple[str, ...] | None) -> bool:
    return bool((groups and set(principal.groups) & set(groups)) or (roles and set(principal.roles) & set(roles)))


def can_use(principal: Principal, agent: AgentDefinition) -> bool:
    """The agent is listed to the caller and the caller may call it."""
    scope = principal.scope.agents
    if scope and agent.id not in scope:
        return False
    access = agent.access
    if access is None or not access.restricts_use:
        return True
    return _matches(principal, access.groups, access.roles)


def can_write(principal: Principal, agent: AgentDefinition) -> bool:
    """The agent's approval-gated tools are offered to the caller."""
    if not can_use(principal, agent) or not principal.scope.write:
        return False
    access = agent.access
    if access is None or not access.restricts_write:
        return True
    return _matches(principal, access.write_groups, access.write_roles)


def can_hand_off(principal: Principal, target: AgentDefinition) -> bool:
    return can_use(principal, target)


def visible_agents(principal: Principal, agents: Mapping[str, AgentDefinition]) -> list[AgentDefinition]:
    return [a for a in agents.values() if can_use(principal, a)]


def access_warnings(agents: Iterable[AgentDefinition], known_groups: set[str] | None,
                    known_roles: set[str] | None) -> list[str]:
    """Problems an operator should look at but that do not stop startup.

    ``known_*`` are the names present in the directory copy; ``None`` skips the typo check (no copy yet).
    """
    out: list[str] = []
    for agent in agents:
        a = agent.access
        if a is None:
            continue
        if a.groups is not None:
            for name in a.write_groups or ():
                if name not in a.groups:
                    out.append(f"agent {agent.id}: write group {name!r} is not in groups, so its members alone cannot reach the agent")
        if a.roles is not None:
            for name in a.write_roles or ():
                if name not in a.roles:
                    out.append(f"agent {agent.id}: write role {name!r} is not in roles, so its holders alone cannot reach the agent")
        if known_groups is not None:
            for name in (*(a.groups or ()), *(a.write_groups or ())):
                if name not in known_groups:
                    out.append(f"agent {agent.id}: group {name!r} is in no directory entry")
        if known_roles is not None:
            for name in (*(a.roles or ()), *(a.write_roles or ())):
                if name not in known_roles:
                    out.append(f"agent {agent.id}: role {name!r} is in no directory entry")
    return out
