"""Strict GitHub issue task-block parser for automatic dispatch."""
from __future__ import annotations

import json
import re
from typing import Any

from .contracts import SpecialistTask

_BLOCK = re.compile(r"```adk-task\s*\n(.*?)\n```", re.DOTALL)


def parse_task_block(body: str) -> SpecialistTask:
    matches = _BLOCK.findall(body)
    if len(matches) != 1:
        raise ValueError("a story requires exactly one fenced adk-task JSON block")
    try:
        raw: Any = json.loads(matches[0])
    except json.JSONDecodeError as error:
        raise ValueError("adk-task block must contain JSON") from error
    return SpecialistTask.model_validate(raw)
