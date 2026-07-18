"""Strict GitHub issue task-block parser for automatic dispatch."""
from __future__ import annotations

import json
import re
from typing import Any

from .contracts import SpecialistTask

_BLOCK = re.compile(r"```adk-task\s*\n(.*?)\n```", re.DOTALL)


def parse_task_block(body: str, *, dispatch_id: str | None = None) -> SpecialistTask:
    matches = _BLOCK.findall(body)
    if len(matches) != 1:
        raise ValueError("a story requires exactly one fenced adk-task JSON block")
    try:
        raw: Any = json.loads(matches[0])
    except json.JSONDecodeError as error:
        raise ValueError("adk-task block must contain JSON") from error
    if dispatch_id is not None:
        if not isinstance(raw, dict) or "dispatch_id" in raw:
            raise ValueError("dispatch ID is service-owned and must not appear in an adk-task block")
        raw["dispatch_id"] = dispatch_id
    return SpecialistTask.model_validate(raw)
