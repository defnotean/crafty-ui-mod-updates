import json
import logging
import os
import re

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared.modrinth_manager import ModrinthManager
from app.classes.shared.mod_update_manager import ModUpdateManager

logger = logging.getLogger(__name__)

install_schema = {
    "type": "object",
    "properties": {
        "content_type": {
            "type": "string",
            "enum": ["mod", "plugin", "datapack", "resourcepack", "shader"],
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
    """Install Modrinth content onto an existing server.

    GET  -> the server's inferred loader + Minecraft version, so the UI can show
            only compatible versions.
    POST -> install: mod -> mods/ (plus required dependencies, recursively);
            datapack -> <level-name>/datapacks/; resourcepack -> server.properties;
            shader -> rejected (client-side).
    """

    def _authorize(self, server_id):
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
        if EnumPermissionsServer.FILES not in self.controller.server_perms.get_permissions(
            mask
        ):
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

    def _server_compat(self, server_id, server_data=None):
        srv = server_data or self.controller.servers.get_server_data_by_id(server_id) or {}
        loader = ModUpdateManager.infer_loader(srv)
        try:
            stats = self.controller.servers.get_server_stats_by_id(server_id)
        except Exception:  # noqa: BLE001
            stats = {}
        game_version = ModUpdateManager.infer_game_version(stats, srv)
        return loader, game_version

    def get(self, server_id):
        auth_data = self._authorize(server_id)
        if not auth_data:
            return
        loader, game_version = self._server_compat(server_id)
        return self.finish_json(
            200,
            {"status": "ok", "data": {"loader": loader, "game_version": game_version}},
        )

    def post(self, server_id):
        auth_data = self._authorize(server_id)
        if not auth_data:
            return
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

        # Compatibility: use the request values if supplied, else the server's.
        loader = data.get("loader") or ModUpdateManager.infer_loader(srv)
        game_version = data.get("game_version")
        if not game_version:
            _, game_version = self._server_compat(server_id, srv)

        mgr = ModrinthManager()
        try:
            if data.get("version_id"):
                version = mgr.version(data["version_id"])
            else:
                versions = mgr.versions(
                    data["project_id"],
                    loaders=[loader]
                    if (loader and content_type in ("mod", "plugin"))
                    else None,
                    game_versions=[game_version] if game_version else None,
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

        # --- mods/plugins: install the file + all required deps recursively ---
        if content_type in ("mod", "plugin"):
            target_dir = os.path.join(
                server_path, "mods" if content_type == "mod" else "plugins"
            )
            visited = set()
            if version.get("project_id"):
                visited.add(version["project_id"])
            installed = []
            self._install_mod_recursive(
                mgr, version, target_dir, loader, game_version, visited, installed, 0
            )
            if not installed:
                return self.finish_json(
                    404, {"status": "error", "error": "NO_FILE", "error_data": "version has no downloadable file"}
                )
            self._audit(auth_data, server_id, content_type)
            return self.finish_json(
                200,
                {
                    "status": "ok",
                    "data": {
                        "installed": {
                            "content_type": content_type,
                            "files": installed,
                            "count": len(installed),
                            "dependencies": max(0, len(installed) - 1),
                        }
                    },
                },
            )

        # --- datapack / resourcepack: single file, no dependencies ---
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
            if content_type == "datapack":
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

    def _install_mod_recursive(
        self, mgr, version, mods_dir, loader, game_version, visited, installed, depth
    ):
        """Download a mod version's file then recurse into its required Modrinth
        dependencies, picking a version compatible with the server. Guarded
        against cycles (visited project ids) and runaway depth."""
        if depth > 6:
            return
        pfile = ModrinthManager.primary_file(version)
        url = pfile.get("url", "")
        sha512 = (pfile.get("hashes") or {}).get("sha512")
        fn = pfile.get("filename")
        if url:
            try:
                mgr.download_to(mods_dir, url, sha512, fn)
                installed.append(fn or os.path.basename(str(url).split("?")[0]))
            except Exception as e:  # noqa: BLE001
                logger.warning("mod/dependency download failed (%s): %s", fn, e)

        for dep in (version.get("dependencies") or []):
            if dep.get("dependency_type") != "required":
                continue
            proj = dep.get("project_id")
            ver_id = dep.get("version_id")
            if not proj and not ver_id:
                continue
            if proj and proj in visited:
                continue
            if proj:
                visited.add(proj)
            try:
                if ver_id:
                    dep_version = mgr.version(ver_id)
                else:
                    dep_versions = mgr.versions(
                        proj,
                        loaders=[loader] if loader else None,
                        game_versions=[game_version] if game_version else None,
                    )
                    if not dep_versions:  # fall back to any loader-matching version
                        dep_versions = mgr.versions(
                            proj, loaders=[loader] if loader else None
                        )
                    dep_version = dep_versions[0] if dep_versions else None
                if dep_version:
                    dep_proj = dep_version.get("project_id")
                    if dep_proj:
                        visited.add(dep_proj)
                    self._install_mod_recursive(
                        mgr, dep_version, mods_dir, loader, game_version, visited, installed, depth + 1
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("dependency install failed for %s: %s", proj or ver_id, e)

    def _audit(self, auth_data, server_id, content_type):
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"installed a {content_type} from Modrinth onto server {server_id}",
            server_id,
            self.get_remote_ip(),
        )
