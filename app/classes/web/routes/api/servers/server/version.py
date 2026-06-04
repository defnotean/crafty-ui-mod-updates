import logging
import json
import os
import re

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

# Java server jar types that support a clean in-place jar swap (non-modded).
UPGRADABLE_TYPES = {"vanilla", "paper", "purpur", "folia", "fabric"}

upgrade_schema = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "minLength": 1},
        "type": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "restart": {"type": "boolean"},
        "agree_to_eula": {"type": "boolean"},
    },
    "required": ["category", "type", "version"],
    "additionalProperties": False,
}


def version_sort_key(value):
    """Minecraft-aware sort key: dotted numeric parts compared as ints, and a
    release (no suffix) ranks above a pre-release/rc/snapshot of the same numbers."""
    match = re.match(r"^\s*(\d+(?:\.\d+)*)(.*)$", str(value))
    if not match:
        return (0, (), 0, str(value))
    nums = tuple(int(n) for n in match.group(1).split("."))
    is_release = 1 if match.group(2).strip() == "" else 0
    return (1, nums, is_release, str(value))


def is_newer(candidate, current):
    if not current:
        return False
    return version_sort_key(candidate) > version_sort_key(current)


class ApiServersServerVersionHandler(BaseApiHandler):
    """GET  -> the server's current version + which catalog versions are newer.
    POST -> upgrade the server to a chosen category|type|version (1-click)."""

    def _authorize(self, server_id, required):
        auth_data = self.authenticate_user()
        if not auth_data:
            return None
        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
            return None
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        permissions = self.controller.server_perms.get_permissions(mask)
        for perm in required:
            if perm not in permissions:
                self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "NOT_AUTHORIZED",
                        "error_data": self.helper.translation.translate(
                            "validators", "insufficientPerms", auth_data[4]["lang"]
                        ),
                    },
                )
                return None
        return auth_data

    def _current(self, server_id):
        """Best-effort (jar_type, mc_version) for the server."""
        version = ""
        try:
            stats = self.controller.servers.get_server_stats_by_id(server_id)
            if isinstance(stats, dict):
                version = str(stats.get("version") or "")
            else:
                version = str(getattr(stats, "version", "") or "")
        except Exception:
            version = ""
        match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", version)
        mc_version = match.group(0) if match else ""

        jar_type = ""
        url_version = ""
        try:
            data = self.controller.servers.get_server_data_by_id(server_id) or {}
            url = str(data.get("executable_update_url") or "")
            base = str(getattr(self.controller.big_bucket, "base_url", "")).rstrip("/")
            if url and base and url.startswith(base):
                parts = url[len(base):].strip("/").split("/")
                jar_type = parts[0] if len(parts) > 0 else ""
                url_version = parts[1] if len(parts) > 1 else ""
        except Exception:
            jar_type = ""
        if not mc_version:
            mc_version = url_version
        return jar_type, mc_version

    def get(self, server_id):
        auth_data = self._authorize(server_id, [EnumPermissionsServer.CONFIG])
        if not auth_data:
            return
        categories = self.controller.big_bucket.get_bucket_data() or {}
        jar_type, current = self._current(server_id)

        category = ""
        available = []
        if jar_type:
            for cat, cval in categories.items():
                types = (cval or {}).get("types", {})
                if jar_type in types:
                    category = cat
                    available = list((types[jar_type].get("versions", {}) or {}).keys())
                    break
        available = sorted(available, key=version_sort_key, reverse=True)
        newer = [v for v in available if is_newer(v, current)]

        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": {
                    "current": current,
                    "type": jar_type,
                    "category": category,
                    "upgradable": jar_type in UPGRADABLE_TYPES,
                    "available": available,
                    "newer": newer,
                },
            },
        )

    def post(self, server_id):
        auth_data = self._authorize(
            server_id, [EnumPermissionsServer.CONFIG, EnumPermissionsServer.BACKUP]
        )
        if not auth_data:
            return
        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, upgrade_schema)
        except ValidationError as why:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": str(why.message),
                },
            )

        category = data["category"]
        jar_type = data["type"]
        version = data["version"]

        if jar_type not in UPGRADABLE_TYPES:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "UNSUPPORTED_FOR_MODDED",
                    "error_data": (
                        "1-click version upgrade currently supports vanilla, paper, "
                        "purpur, folia and fabric servers."
                    ),
                },
            )

        url = self.controller.big_bucket.get_fetch_url(category, jar_type, version)
        if not url:
            return self.finish_json(
                404,
                {
                    "status": "error",
                    "error": "VERSION_NOT_FOUND",
                    "error_data": f"No download found for {category} | {jar_type} | {version}.",
                },
            )

        # The upgrade flow backs up first and silently aborts without a backup
        # config, so ensure a default one exists (keeps this truly 1-click).
        try:
            if not self.controller.management.get_backups_by_server(server_id, True):
                self.controller.management.add_default_backup_config(
                    server_id, os.path.join(self.helper.backup_path, server_id)
                )
        except Exception as e:
            logger.warning("version upgrade: could not ensure backup config: %s", e)

        server_obj = self.controller.servers.get_server_obj(server_id)
        server_obj.executable_update_url = url
        self.controller.servers.update_server(server_obj)

        if data.get("agree_to_eula"):
            try:
                srv = self.controller.servers.get_server_data_by_id(server_id) or {}
                if srv.get("path"):
                    with open(
                        os.path.join(srv["path"], "eula.txt"), "w", encoding="utf-8"
                    ) as eula:
                        eula.write("eula=true")
            except Exception as e:
                logger.warning("version upgrade: could not write eula: %s", e)

        # Reuse Crafty's existing backup -> stop -> swap-in-place -> restart flow.
        self.controller.management.send_command(
            auth_data[4]["user_id"],
            server_id,
            self.get_remote_ip(),
            "update_executable",
        )

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"started a version upgrade to {jar_type} {version} for server {server_id}",
            server_id,
            self.get_remote_ip(),
        )

        return self.finish_json(
            200,
            {"status": "ok", "data": {"executable_update_url": url, "queued": True}},
        )
