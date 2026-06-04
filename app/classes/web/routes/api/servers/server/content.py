import json
import logging
import os
import re

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared.modrinth_manager import ModrinthManager

logger = logging.getLogger(__name__)

install_schema = {
    "type": "object",
    "properties": {
        "content_type": {
            "type": "string",
            "enum": ["mod", "datapack", "resourcepack", "shader"],
        },
        "project_id": {"type": "string", "minLength": 1},
        "version_id": {"type": "string"},
        "loader": {"type": "string"},
        "game_version": {"type": "string"},
    },
    "required": ["content_type", "project_id"],
    "additionalProperties": False,
}


def _read_property(text, key, default=""):
    match = re.search(rf"(?mi)^\s*{re.escape(key)}\s*=\s*(.*)$", text)
    return match.group(1).strip() if match else default


def _set_property(text, key, value):
    pattern = re.compile(rf"(?mi)^\s*{re.escape(key)}\s*=.*$")
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    sep = "" if (not text or text.endswith("\n")) else "\n"
    return text + sep + line + "\n"


class ApiServersServerContentHandler(BaseApiHandler):
    """Install Modrinth content onto an existing server:
    mod -> mods/, datapack -> <level-name>/datapacks/, resourcepack -> server.properties,
    shader -> rejected (client-side)."""

    def post(self, server_id):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        if EnumPermissionsServer.FILES not in self.controller.server_perms.get_permissions(mask):
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
        try:
            data = json.loads(self.request.body)
            validate(data, install_schema)
        except (json.JSONDecodeError, ValidationError) as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )

        content_type = data["content_type"]
        if content_type == "shader":
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "CLIENT_SIDE_CONTENT",
                    "error_data": "Shaders are client-side — players install them in their own client, not on the server.",
                },
            )

        srv = self.controller.servers.get_server_data_by_id(server_id) or {}
        server_path = srv.get("path")
        if not server_path or not os.path.isdir(server_path):
            return self.finish_json(
                404,
                {"status": "error", "error": "SERVER_NOT_FOUND", "error_data": "server path not found"},
            )

        if content_type == "mod":
            try:
                stats = self.controller.servers.get_server_stats_by_id(server_id)
                running = stats.get("running") if isinstance(stats, dict) else getattr(stats, "running", False)
                if running:
                    return self.finish_json(
                        409,
                        {"status": "error", "error": "SERVER_RUNNING", "error_data": "Stop the server before installing mods."},
                    )
            except Exception:
                pass

        mgr = ModrinthManager()
        try:
            if data.get("version_id"):
                version = mgr.version(data["version_id"])
            else:
                versions = mgr.versions(
                    data["project_id"],
                    loaders=[data["loader"]] if data.get("loader") else None,
                    game_versions=[data["game_version"]] if data.get("game_version") else None,
                )
                if not versions:
                    return self.finish_json(
                        404, {"status": "error", "error": "NO_VERSION", "error_data": "No compatible version found."}
                    )
                version = versions[0]
        except Exception as e:
            return self.finish_json(
                502, {"status": "error", "error": "MODRINTH_ERROR", "error_data": str(e)}
            )

        pfile = ModrinthManager.primary_file(version)
        url = pfile.get("url", "")
        hashes = pfile.get("hashes") or {}
        sha512 = hashes.get("sha512")
        filename = pfile.get("filename")
        if not url:
            return self.finish_json(
                404, {"status": "error", "error": "NO_FILE", "error_data": "version has no downloadable file"}
            )

        try:
            if content_type == "mod":
                path = mgr.download_to(os.path.join(server_path, "mods"), url, sha512, filename)
            elif content_type == "datapack":
                props_path = os.path.join(server_path, "server.properties")
                level = "world"
                if os.path.isfile(props_path):
                    with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                        level = _read_property(f.read(), "level-name", "world") or "world"
                path = mgr.download_to(os.path.join(server_path, level, "datapacks"), url, sha512, filename)
            elif content_type == "resourcepack":
                props_path = os.path.join(server_path, "server.properties")
                text = ""
                if os.path.isfile(props_path):
                    with open(props_path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                text = _set_property(text, "resource-pack", url)
                if hashes.get("sha1"):
                    text = _set_property(text, "resource-pack-sha1", hashes["sha1"])
                with open(props_path, "w", encoding="utf-8") as f:
                    f.write(text)
                self._audit(auth_data, server_id, "resourcepack")
                return self.finish_json(
                    200,
                    {"status": "ok", "data": {"installed": {"content_type": "resourcepack", "resource_pack_url": url}}},
                )
            else:
                return self.finish_json(
                    400, {"status": "error", "error": "BAD_TYPE", "error_data": "unsupported content_type"}
                )
        except Exception as e:
            logger.warning("content install failed: %s", e)
            return self.finish_json(
                500, {"status": "error", "error": "INSTALL_FAILED", "error_data": str(e)}
            )

        self._audit(auth_data, server_id, content_type)
        return self.finish_json(
            200,
            {"status": "ok", "data": {"installed": {"content_type": content_type, "filename": os.path.basename(str(path))}}},
        )

    def _audit(self, auth_data, server_id, content_type):
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"installed a {content_type} from Modrinth onto server {server_id}",
            server_id,
            self.get_remote_ip(),
        )
