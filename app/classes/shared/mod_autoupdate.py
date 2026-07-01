"""Scheduled automatic updates for mods and the Minecraft version.

Per-server config (mod auto-update, Minecraft auto-update, and check frequency)
lives in a small JSON file in the server directory. An hourly scheduler tick
checks every Java server and, when one is due:

  * upgrades the server jar to the latest Minecraft release (vanilla/paper/
    purpur/folia/fabric), reusing Crafty's backup -> stop -> swap -> restart
    flow — the auto-Java provisioner then pulls the matching Java; and/or
  * scans Modrinth for mod updates and applies them.

Everything only acts when there is actually something to do, so an up-to-date
server is never restarted.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CONFIG_NAME = "crafty_mod_autoupdate.json"
FREQUENCIES = {"daily": 1, "weekly": 7, "monthly": 30}  # days between checks
DEFAULT_FREQ = "weekly"

# Jar types that support a clean in-place version swap (non-modded loaders + fabric)
UPGRADABLE_TYPES = {"vanilla", "paper", "purpur", "folia", "fabric"}


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #
def _config_path(server_path):
    return os.path.join(server_path, CONFIG_NAME)


def get_config(server_path):
    cfg = {
        "enabled": False,
        "update_minecraft": False,
        "frequency": DEFAULT_FREQ,
        "last_check": None,
    }
    try:
        path = _config_path(server_path)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                cfg["enabled"] = bool(data.get("enabled", False))
                cfg["update_minecraft"] = bool(data.get("update_minecraft", False))
                freq = str(data.get("frequency", DEFAULT_FREQ)).lower()
                cfg["frequency"] = freq if freq in FREQUENCIES else DEFAULT_FREQ
                cfg["last_check"] = data.get("last_check")
    except Exception as exc:  # noqa: BLE001
        logger.debug("mod-autoupdate read failed for %s: %s", server_path, exc)
    return cfg


def set_config(
    server_path, enabled, frequency, last_check=None, update_minecraft=False
):
    freq = str(frequency or DEFAULT_FREQ).lower()
    if freq not in FREQUENCIES:
        freq = DEFAULT_FREQ
    cfg = {
        "enabled": bool(enabled),
        "update_minecraft": bool(update_minecraft),
        "frequency": freq,
        "last_check": last_check,
    }
    try:
        with open(_config_path(server_path), "w", encoding="utf-8") as handle:
            json.dump(cfg, handle)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mod-autoupdate write failed for %s: %s", server_path, exc)
    return cfg


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _is_due(cfg):
    if not (cfg.get("enabled") or cfg.get("update_minecraft")):
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


# --------------------------------------------------------------------------- #
#  Version comparison (Minecraft-aware)
# --------------------------------------------------------------------------- #
def _version_sort_key(value):
    match = re.match(r"^\s*(\d+(?:\.\d+)*)(.*)$", str(value))
    if not match:
        return (0, (), 0, str(value))
    nums = tuple(int(n) for n in match.group(1).split("."))
    is_release = 1 if match.group(2).strip() == "" else 0
    return (1, nums, is_release, str(value))


def _is_newer(candidate, current):
    if not current:
        return False
    return _version_sort_key(candidate) > _version_sort_key(current)


def _latest_release(versions):
    """Newest pure-release version (e.g. 26.1.2), ignoring snapshots/pre-releases."""
    releases = [v for v in versions if re.match(r"^\d+(?:\.\d+)+$", str(v))]
    if not releases:
        return None
    return max(releases, key=_version_sort_key)


# --------------------------------------------------------------------------- #
#  Scheduler tick
# --------------------------------------------------------------------------- #
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
                "mod-autoupdate: checking '%s' (mods=%s, minecraft=%s, %s)",
                server.get("server_name"),
                cfg["enabled"],
                cfg["update_minecraft"],
                cfg["frequency"],
            )
            try:
                _update_one(controller, str(server.get("server_id")), path, server, cfg)
            finally:
                set_config(
                    path,
                    cfg["enabled"],
                    cfg["frequency"],
                    _now_iso(),
                    cfg.get("update_minecraft", False),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mod-autoupdate: error on server %s: %s",
                server.get("server_id"),
                exc,
            )


def _update_one(controller, server_id, path, server_data, cfg):
    # Minecraft version first (so mods then match the new version). The upgrade
    # restarts the server through Crafty's flow, so if it fires we leave mods for
    # the next cycle.
    if cfg.get("update_minecraft"):
        try:
            if _try_mc_upgrade(controller, server_id, path, server_data):
                logger.info(
                    "mod-autoupdate: %s queued a Minecraft upgrade; mods next cycle",
                    server_id,
                )
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "mod-autoupdate: minecraft upgrade failed for %s: %s", server_id, exc
            )
    if cfg.get("enabled"):
        _update_mods(controller, server_id, path, server_data)


# --------------------------------------------------------------------------- #
#  Minecraft version auto-upgrade
# --------------------------------------------------------------------------- #
def _current_version(controller, server_id):
    """Best-effort (jar_type, mc_version, category) for a server."""
    version = ""
    try:
        stats = controller.servers.get_server_stats_by_id(server_id)
        version = (
            str((stats or {}).get("version") or "") if isinstance(stats, dict) else ""
        )
    except Exception:  # noqa: BLE001
        version = ""
    match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", version)
    mc_version = match.group(0) if match else ""

    jar_type = ""
    url_version = ""
    try:
        data = controller.servers.get_server_data_by_id(server_id) or {}
        url = str(data.get("executable_update_url") or "")
        base = str(getattr(controller.big_bucket, "base_url", "")).rstrip("/")
        if url and base and url.startswith(base):
            parts = url[len(base) :].strip("/").split("/")
            jar_type = parts[0] if parts else ""
            url_version = parts[1] if len(parts) > 1 else ""
    except Exception:  # noqa: BLE001
        jar_type = ""
    if not mc_version:
        mc_version = url_version
    return jar_type, mc_version


def _try_mc_upgrade(controller, server_id, path, server_data):
    """Upgrade the server to the latest Minecraft release if one exists. Returns
    True if an upgrade was queued."""
    from app.classes.models.users import HelperUsers

    jar_type, current = _current_version(controller, server_id)
    if not jar_type or jar_type not in UPGRADABLE_TYPES:
        return False
    try:
        categories = controller.big_bucket.get_bucket_data() or {}
    except Exception:  # noqa: BLE001
        return False

    category = ""
    versions = []
    for cat, cval in categories.items():
        types = (cval or {}).get("types", {})
        if jar_type in types:
            category = cat
            versions = list((types[jar_type].get("versions", {}) or {}).keys())
            break
    latest = _latest_release(versions)
    if not latest or not _is_newer(latest, current):
        return False

    url = controller.big_bucket.get_fetch_url(category, jar_type, latest)
    if not url:
        return False

    # The upgrade flow aborts silently without a backup config; ensure one.
    try:
        if not controller.management.get_backups_by_server(server_id, True):
            controller.management.add_default_backup_config(
                server_id, os.path.join(controller.helper.backup_path, server_id)
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("mod-autoupdate: backup config ensure failed: %s", exc)

    server_obj = controller.servers.get_server_obj(server_id)
    server_obj.executable_update_url = url
    controller.servers.update_server(server_obj)

    eula_path = os.path.join(path, "eula.txt")
    eula_accepted = False
    if os.path.isfile(eula_path):
        try:
            with open(eula_path, "r", encoding="utf-8") as eula:
                line = eula.readline().strip().lower().replace(" ", "")
            eula_accepted = line == "eula=true"
        except Exception:  # noqa: BLE001
            pass
    if not eula_accepted:
        try:
            with open(eula_path, "w", encoding="utf-8") as eula:
                eula.write("eula=false")
        except Exception as exc:  # noqa: BLE001
            logger.warning("mod-autoupdate: could not write eula.txt: %s", exc)
        else:
            logger.warning(
                "mod-autoupdate: Minecraft upgrade requires manual EULA acceptance; "
                "created eula.txt with eula=false — server will not auto-start"
            )

    system_user = HelperUsers.get_user_id_by_name("system")
    controller.management.send_command(
        system_user, server_id, "127.0.0.1", "update_executable"
    )
    logger.info(
        "mod-autoupdate: upgrading %s from %s to %s (%s)",
        server_id,
        current or "?",
        latest,
        jar_type,
    )
    if not eula_accepted:
        logger.info(
            "mod-autoupdate: %s upgrade queued; auto-start skipped until EULA accepted",
            server_id,
        )
    return True


# --------------------------------------------------------------------------- #
#  Mod auto-update
# --------------------------------------------------------------------------- #
def _update_mods(controller, server_id, path, server_data):
    from app.classes.shared.mod_update_manager import ModUpdateManager

    loader = ModUpdateManager.infer_loader(server_data)
    try:
        stats = controller.servers.get_server_stats_by_id(server_id)
    except Exception:  # noqa: BLE001
        stats = {}
    game_version = ModUpdateManager.infer_game_version(stats, server_data)
    manager = ModUpdateManager(path)

    try:
        scan = manager.scan(loader, game_version)
    except Exception as exc:  # noqa: BLE001
        logger.info("mod-autoupdate: scan failed for %s: %s", server_id, exc)
        return
    pending = [m for m in scan.get("mods", []) if m.get("status") == "update_available"]
    if not pending:
        logger.info("mod-autoupdate: %s mods already up to date", server_id)
        return

    server = controller.servers.get_server_instance_by_id(server_id)
    was_running = False
    try:
        was_running = bool(server.check_running())
    except Exception:  # noqa: BLE001
        pass

    if was_running:
        logger.info(
            "mod-autoupdate: stopping %s to apply %d mod update(s)",
            server_id,
            len(pending),
        )
        try:
            server.stop_server()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mod-autoupdate: stop failed for %s: %s", server_id, exc)
            return
        for _ in range(90):
            try:
                if not server.check_running():
                    break
            except Exception:  # noqa: BLE001
                break
            time.sleep(1)

    try:
        manager.update_available(loader, game_version)
        logger.info(
            "mod-autoupdate: applied %d mod update(s) to %s", len(pending), server_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("mod-autoupdate: mod update failed for %s: %s", server_id, exc)

    if was_running:
        try:
            server.run_threaded_server(None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("mod-autoupdate: restart failed for %s: %s", server_id, exc)
