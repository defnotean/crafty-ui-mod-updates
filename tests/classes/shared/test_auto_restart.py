from zoneinfo import ZoneInfo

from apscheduler.jobstores.base import JobLookupError

from app.classes.shared.server import ServerInstance


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
    return server


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
