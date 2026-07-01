import json
from unittest.mock import MagicMock, patch

import app.classes.shared.mod_autoupdate as mod_autoupdate


def test_get_config_defaults_when_missing(tmp_path):
    cfg = mod_autoupdate.get_config(str(tmp_path))
    assert cfg == {
        "enabled": False,
        "update_minecraft": False,
        "frequency": "weekly",
        "last_check": None,
    }


def test_get_config_loads_from_json(tmp_path):
    config_path = tmp_path / mod_autoupdate.CONFIG_NAME
    config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "update_minecraft": True,
                "frequency": "daily",
                "last_check": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    cfg = mod_autoupdate.get_config(str(tmp_path))

    assert cfg["enabled"] is True
    assert cfg["update_minecraft"] is True
    assert cfg["frequency"] == "daily"
    assert cfg["last_check"] == "2026-01-01T00:00:00+00:00"


def test_get_config_normalizes_invalid_frequency(tmp_path):
    config_path = tmp_path / mod_autoupdate.CONFIG_NAME
    config_path.write_text(json.dumps({"frequency": "hourly"}), encoding="utf-8")

    cfg = mod_autoupdate.get_config(str(tmp_path))

    assert cfg["frequency"] == "weekly"


def test_set_config_persists_values(tmp_path):
    saved = mod_autoupdate.set_config(
        str(tmp_path), enabled=True, frequency="monthly", update_minecraft=True
    )

    assert saved["enabled"] is True
    assert saved["frequency"] == "monthly"
    assert saved["update_minecraft"] is True
    assert mod_autoupdate.get_config(str(tmp_path)) == saved


def test_update_mods_skips_restart_when_no_updates(tmp_path):
    controller = MagicMock()
    server = MagicMock()
    server.check_running.return_value = True
    stop_calls = []
    restart_calls = []
    server.stop_server = lambda: stop_calls.append(True)
    server.run_threaded_server = lambda _: restart_calls.append(True)
    controller.servers.get_server_instance_by_id.return_value = server
    controller.servers.get_server_stats_by_id.return_value = {"version": "1.21.1"}

    fake_manager = MagicMock()
    fake_manager.scan.return_value = {
        "mods": [
            {"status": "up_to_date"},
            {"status": "recognized"},
        ]
    }

    with patch(
        "app.classes.shared.mod_update_manager.ModUpdateManager",
        return_value=fake_manager,
    ) as mock_cls:
        mock_cls.infer_loader.return_value = "fabric"
        mock_cls.infer_game_version.return_value = "1.21.1"

        mod_autoupdate._update_mods(controller, "server-1", str(tmp_path), {})

    assert stop_calls == []
    assert restart_calls == []
    fake_manager.update_available.assert_not_called()


def test_try_mc_upgrade_eula_gate_writes_false_when_not_accepted(tmp_path, monkeypatch):
    """When EULA is not yet accepted, create eula.txt with eula=false."""
    controller = _mock_mc_upgrade_controller(tmp_path)
    monkeypatch.setattr(
        "app.classes.models.users.HelperUsers.get_user_id_by_name",
        lambda _name: 1,
    )

    queued = mod_autoupdate._try_mc_upgrade(
        controller, "server-1", str(tmp_path), {"type": "minecraft-java"}
    )

    assert queued is True
    eula_path = tmp_path / "eula.txt"
    assert eula_path.is_file()
    assert eula_path.read_text(encoding="utf-8") == "eula=false"
    controller.management.send_command.assert_called_once_with(
        1, "server-1", "127.0.0.1", "update_executable"
    )


def test_try_mc_upgrade_eula_gate_skips_false_when_already_accepted(
    tmp_path, monkeypatch
):
    """When eula=true already exists, do not overwrite it."""
    (tmp_path / "eula.txt").write_text("eula=true", encoding="utf-8")
    controller = _mock_mc_upgrade_controller(tmp_path)
    monkeypatch.setattr(
        "app.classes.models.users.HelperUsers.get_user_id_by_name",
        lambda _name: 1,
    )

    queued = mod_autoupdate._try_mc_upgrade(
        controller, "server-1", str(tmp_path), {"type": "minecraft-java"}
    )

    assert queued is True
    assert (tmp_path / "eula.txt").read_text(encoding="utf-8") == "eula=true"
    controller.management.send_command.assert_called_once()


def _mock_mc_upgrade_controller(tmp_path):
    controller = MagicMock()
    controller.servers.get_server_stats_by_id.return_value = {"version": "1.21.0"}
    controller.servers.get_server_data_by_id.return_value = {
        "executable_update_url": "https://example.test/vanilla/1.21.0/server.jar"
    }
    controller.big_bucket.base_url = "https://example.test"
    controller.big_bucket.get_bucket_data.return_value = {
        "release": {
            "types": {
                "vanilla": {"versions": {"1.21.0": {}, "1.21.1": {}}},
            }
        }
    }
    controller.big_bucket.get_fetch_url.return_value = (
        "https://example.test/vanilla/1.21.1/server.jar"
    )
    controller.management.get_backups_by_server.return_value = [{"id": 1}]
    controller.servers.get_server_obj.return_value = MagicMock()
    return controller


def test_run_checks_skips_servers_not_due(tmp_path, monkeypatch):
    controller = MagicMock()
    config_path = tmp_path / mod_autoupdate.CONFIG_NAME
    config_path.write_text(
        json.dumps(
            {
                "enabled": True,
                "frequency": "weekly",
                "last_check": mod_autoupdate._now_iso(),
            }
        ),
        encoding="utf-8",
    )
    controller.servers.get_all_defined_servers.return_value = [
        {
            "server_id": "server-1",
            "path": str(tmp_path),
            "type": "minecraft-java",
            "server_name": "Test",
        }
    ]
    update_one = MagicMock()
    monkeypatch.setattr(mod_autoupdate, "_update_one", update_one)

    mod_autoupdate.run_checks(controller)

    update_one.assert_not_called()
