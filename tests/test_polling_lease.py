from datetime import datetime, timedelta, timezone

from adk_agents.operational_record import OperationalRecord, PollingLease


def test_sqlite_polling_lease_allows_only_one_live_worker(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    first = PollingLease(record, project_id="project-1", owner_id="one", now=lambda: now)
    second = PollingLease(record, project_id="project-1", owner_id="two", now=lambda: now)

    assert first.acquire() is True
    assert second.acquire() is False


def test_sqlite_polling_lease_can_be_taken_after_expiry(tmp_path):
    record = OperationalRecord(tmp_path / "record.sqlite3")
    record.startup()
    clock = [datetime(2026, 7, 18, tzinfo=timezone.utc)]
    first = PollingLease(record, project_id="project-1", owner_id="one", duration=timedelta(seconds=30), now=lambda: clock[0])
    second = PollingLease(record, project_id="project-1", owner_id="two", duration=timedelta(seconds=30), now=lambda: clock[0])

    assert first.acquire() is True
    clock[0] += timedelta(seconds=31)
    assert second.acquire() is True
