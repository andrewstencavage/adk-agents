from datetime import datetime, timedelta, timezone
import json
import pytest
from adk_agents.task_format import parse_task_block

def payload():
    return {"control_issue_ref":"#1","story_ref":"#20","dispatch_id":"dispatch-0001","specialist":"research","objective":"x","acceptance_criteria":["y"],"requested_by":"andrew","deadline":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),"budget_steps":1}

def test_requires_one_valid_fenced_task_block():
    task = parse_task_block("text\n```adk-task\n" + json.dumps(payload()) + "\n```")
    assert task.story_ref == "#20"
    with pytest.raises(ValueError): parse_task_block("no block")

def test_service_injects_its_dispatch_id():
    data = payload(); data.pop("dispatch_id")
    task = parse_task_block("```adk-task\n" + json.dumps(data) + "\n```", dispatch_id="dispatch-0002")
    assert task.dispatch_id == "dispatch-0002"
