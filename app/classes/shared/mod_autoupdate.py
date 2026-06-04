"""Scheduled automatic mod updates.

Per-server config (enabled + check frequency) lives in a small JSON file in the
server directory. An hourly scheduler tick checks every Java server and, when one
is due, scans for Modrinth mod updates and applies them — stopping and
restarting the server around the update only when updates actually exist, so an
already up-to-date server is never restarted.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CONFIG_NAME = "crafty_mod_autoupdate.json"
FREQUENCIES = {"daily": 1, "weekly": 7, "monthly": 30}  # days between checks
DEFAULT_FREQ = "weekly"


def _config_path(server_path):
    return os.path.join(server_path, CONFIG_NAME)


def get_config(server_path):
    cfg = {"enabled": False, "frequency": DEFAULT_FREQ, "last_check": None}
    try:
        path = _config_path(server_path)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                cfg["enabled"] = bool(data.get("enabled", False))
                freq = str(data.get("frequency", DEFAULT_FREQ)).lower()
                cfg["frequency"] = freq if freq in FREQUENCIES else DEFAULT_FREQ
                cfg["last_check"] = data.get("last_check")
    except Exception as exc:  # noqa: BLE001
        logger.debug("mod-autoupdate read failed for %s: %s", server_path, exc)
    return cfg


def set_config(server_path, enabled, frequency, last_check=None):
    freq = str(frequency or DEFAULT_FREQ).lower()
    if freq not in FREQUENCIES:
        freq = DEFAULT_FREQ
    cfg = {"enabled": bool(enabled), "frequency": freq, "last_check": last_check}
    try:
        with open(_config_path(server_path), "w", encoding="utf-8") as handle:
            json.dump(cfg, handle)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mod-autoupdate write failed for %s: %s", server_path, exc)
    return cfg


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _is_due(cfg):
    if not cfg.get("enabled"):
        return False
    last = cfg.get("last_check")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:  # noqa: BLE001
        return True
    days = FREQUENCIES.get(cfg.get("frequency"), FREQUENCIES[DEFAULT_FREQ])
    return (datetime.now(timezone.utc) - last_dt).total_seconds() >= days * 86400


def run_checks(controller):
    """Hourly scheduler tick: update any server whose auto-update is due."""
    try:
        servers = controller.servers.get_all_defined_servers()
    except Exception as exc:  # noqa: BLE001
        logger.debug("mod-autoupdate: could not list servers: %s", exc)
        return
    for server in servers:
        path = server.get("path")
        try:
            if not path or not os.path.isdir(path):
                continue
            if server.get("type") != "minecraft-java":
                continue
            cfg = get_config(path)
            if not _is_due(cfg):
                continue
            logger.info(
                "mod-autoupdate: checking '%s' (%s)",
                server.get("server_name"),
                cfg["frequency"],
            )
            try:
                _update_one(controller, str(server.get("server_id")), path, server)
            finally:
                # mark checked regardless of outcome so we don't retry hourly
                set_config(path, cfg["enabled"], cfg["frequency"], _now_iso())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mod-autoupdate: error on server %s: %s",
                server.get("server_id"),
                exc,
            )


def _update_one(controller, server_id, path, server_data):
    from app.classes.shared.mod_update_manager import ModUpdateManager

    loader = ModUpdateManager.infer_loader(server_data)
    try:
        stats = controller.servers.get_server_stats_by_id(server_id)
    except Exception:  # noqa: BLE001
        stats = {}
    game_version = ModUpdateManager.infer_game_version(stats, server_data)
    manager = ModUpdateManager(path)

    # cheap check first (reads only; safe while the server runs)
    try:
        scan = manager.scan(loader, game_version)
    except Exception as exc:  # noqa: BLE001
        logger.info("mod-autoupdate: scan failed for %s: %s", server_id, exc)
        return
    pending = [m for m in scan.get("mods", []) if m.get("status") == "update_available"]
    if not pending:
        logger.info("mod-autoupdate: %s already up to date", server_id)
        return

    server = controller.servers.get_server_instance_by_id(server_id)
    was_running = False
    try:
        was_running = bool(server.check_running())
    except Exception:  # noqa: BLE001
        pass

    if was_running:
        logger.info(
            "mod-autoupdate: stopping %s to apply %d update(s)", server_id, len(pending)
        )
        try:
            server.stop_server()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mod-autoupdate: stop failed for %s: %s", server_id, exc)
            return
        for _ in range(90):  # wait up to ~90s for a clean stop
            try:
                if not server.check_running():
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(1)

    try:
        manager.update_available(loader, game_version)
        logger.info(
            "mod-autoupdate: applied %d update(s) to %s", len(pending), server_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("mod-autoupdate: update failed for %s: %s", server_id, exc)

    if was_running:
        logger.info("mod-autoupdate: restarting %s", server_id)
        try:
            server.run_threaded_server(None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mod-autoupdate: restart failed for %s: %s", server_id, exc)
