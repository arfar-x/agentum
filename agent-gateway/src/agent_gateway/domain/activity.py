"""What the gateway tells the user it is doing, without knowing which client they use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

NARRATION_BLOCK = (
    "Visibility: when you hand the conversation to another agent, or start work that may take a "
    "while or runs in the background, say so in one short line first. If the client's own "
    "instructions describe how to show progress or status (for example a todo, status or notify "
    "tool), use that; otherwise write a plain one-line note. Keep it brief and never repeat it."
)


@dataclass(frozen=True)
class ActivityEvent:
    kind: Literal["handoff", "retrieval", "tool_started", "tool_finished", "waiting"]
    text: str

    def line(self) -> str:
        return self.text


def handoff(from_agent: str, to_agent: str) -> ActivityEvent:
    return ActivityEvent("handoff", f"Handing off from {from_agent} to {to_agent}.")


def retrieval(count: int) -> ActivityEvent:
    return ActivityEvent("retrieval", f"Looked up the knowledge base ({count} result{'s' if count != 1 else ''}).")


def tool_started(name: str) -> ActivityEvent:
    return ActivityEvent("tool_started", f"Running {name}.")


def tool_finished(name: str) -> ActivityEvent:
    return ActivityEvent("tool_finished", f"Finished {name}.")
