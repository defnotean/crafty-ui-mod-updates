from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError

from app.classes.shared.server import (
    AUTO_RESTART_LEGACY_SCHEDULE_COMMAND,
    AUTO_RESTART_LEGACY_SCHEDULE_NAME,
    ServerInstance,
)


class FakeSchedule:
    def __init__(
        self,
        schedule_id=42,
        enabled=True,
        start_time="05:15",
        timezone="America/New_York",
        interval_type="days",
    ):
        self.schedule_id = schedule_id
        self.name = AUTO_RESTART_LEGACY_SCHEDULE_NAME
        self.command = AUTO_RESTART_LEGACY_SCHEDULE_COMMAND
        self.enabled = enabled
        self.start_time = start_time
        self.timezone = timezone
        self.interval_type = interval_type


class FakeManagementHelper:
    def __init__(self, schedules=None):
        self.schedules = list(schedules or [])
        self.deleted = []

    def get_schedules_by_server(self, _server_id):
        return list(self.schedules)

    def get_child_schedules(self, _schedule_id):
        return []

    def delete_scheduled_task(self, schedule_id):
        self.deleted.append(schedule_id)
        self.schedules = [
            schedule
            for schedule in self.schedules
            if schedule.schedule_id != schedule_id
        ]


class FakeServerModel:
    auto_restart = False
    auto_restart_time = "04:00"
    auto_restart_timezone = "UTC"


class FakeJob:
    def __init__(self, trigger):
        self.trigger = trigger
        self.next_run_time = None


class FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.removed = []

    def remove_job(self, job_id):
        if job_id not in self.jobs:
            raise JobLookupError(job_id)
        self.removed.append(job_id)
        del self.jobs[job_id]

    def add_job(self, _func, trigger, id, replace_existing=False):
        if id in self.jobs and not replace_existing:
            raise AssertionError("replace_existing should be used for restart jobs")
        self.jobs[id] = FakeJob(trigger)

    def get_job(self, job_id):
        return self.jobs.get(job_id)


def make_server(settings):
    server = ServerInstance.__new__(ServerInstance)
    server.server_id = "server-1"
    server.name = "Server 1"
    server.tz = ZoneInfo("UTC")
    server.settings = settings
    server.server_scheduler = FakeScheduler()
    server.management_helper = FakeManagementHelper()
    return server


def test_migrate_legacy_auto_restart_schedule_updates_server_columns(monkeypatch):
    legacy = FakeSchedule()
    server = make_server({"auto_restart": False})
    server.management_helper = FakeManagementHelper([legacy])
    server_obj = FakeServerModel()

    monkeypatch.setattr(
        "app.classes.shared.server.HelperServers.get_server_obj",
        lambda _server_id: server_obj,
    )
    monkeypatch.setattr(
        "app.classes.shared.server.HelperServers.update_server",
        lambda _server_obj: None,
    )

    migrated = server.migrate_legacy_auto_restart_schedule()

    assert migrated is True
    assert server_obj.auto_restart is True
    assert server_obj.auto_restart_time == "05:15"
    assert server_obj.auto_restart_timezone == "America/New_York"
    assert server.settings["auto_restart"] is True
    assert server.management_helper.deleted == [42]


def test_migrate_legacy_auto_restart_schedule_keeps_existing_config(monkeypatch):
    legacy = FakeSchedule(start_time="01:00", timezone="Europe/London")
    server = make_server(
        {
            "auto_restart": True,
            "auto_restart_time": "03:30",
            "auto_restart_timezone": "UTC",
        }
    )
    server.management_helper = FakeManagementHelper([legacy])
    server_obj = FakeServerModel()
    server_obj.auto_restart = True
    server_obj.auto_restart_time = "03:30"
    server_obj.auto_restart_timezone = "UTC"
    updates = []

    monkeypatch.setattr(
        "app.classes.shared.server.HelperServers.get_server_obj",
        lambda _server_id: server_obj,
    )
    monkeypatch.setattr(
        "app.classes.shared.server.HelperServers.update_server",
        lambda updated: updates.append(updated),
    )

    migrated = server.migrate_legacy_auto_restart_schedule()

    assert migrated is True
    assert updates == []
    assert server.settings["auto_restart_time"] == "03:30"
    assert server.management_helper.deleted == [42]


def test_migrate_legacy_auto_restart_schedule_uses_global_job_remover():
    legacy = FakeSchedule()
    server = make_server({"auto_restart": True})
    server.management_helper = FakeManagementHelper([legacy])
    removed = []
    ServerInstance.register_global_schedule_job_remover(removed.append)

    try:
        server.migrate_legacy_auto_restart_schedule()
        assert removed == [42]
    finally:
        ServerInstance.register_global_schedule_job_remover(None)


def test_auto_restart_schedule_uses_configured_timezone():
    server = make_server(
        {
            "auto_restart": True,
            "auto_restart_time": "03:30",
            "auto_restart_timezone": "America/Chicago",
        }
    )

    server.sync_auto_restart_schedule()

    job = server.server_scheduler.get_job("server-1_auto_restart")
    assert job is not None
    assert str(job.trigger.timezone) == "America/Chicago"


def test_auto_restart_schedule_is_removed_when_disabled():
    server = make_server(
        {
            "auto_restart": True,
            "auto_restart_time": "03:30",
            "auto_restart_timezone": "UTC",
        }
    )
    server.sync_auto_restart_schedule()
    server.settings["auto_restart"] = False

    server.sync_auto_restart_schedule()

    assert server.server_scheduler.get_job("server-1_auto_restart") is None
    assert server.server_scheduler.removed == ["server-1_auto_restart"]


def test_invalid_auto_restart_timezone_is_skipped():
    server = make_server(
        {
            "auto_restart": True,
            "auto_restart_time": "03:30",
            "auto_restart_timezone": "Not/AZone",
        }
    )

    server.sync_auto_restart_schedule()

    assert server.server_scheduler.get_job("server-1_auto_restart") is None


def test_auto_restart_does_not_start_offline_server(monkeypatch):
    server = make_server(
        {
            "auto_restart": True,
            "auto_restart_time": "03:30",
            "auto_restart_timezone": "UTC",
        }
    )
    restarted = []
    server.check_running = lambda: False
    server.restart_threaded_server = restarted.append
    monkeypatch.setattr(
        "app.classes.shared.server.HelperUsers.get_user_id_by_name",
        lambda _name: -100,
    )

    server.auto_restart_server()

    assert restarted == []
