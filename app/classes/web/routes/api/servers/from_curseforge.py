import json
import logging
import shutil
import tempfile
import threading

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared.curseforge_manager import CurseForgeManager
from app.classes.shared import modpack_installer

logger = logging.getLogger(__name__)

from_curseforge_schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 2, "pattern": r"^[^/\\#]*$"},
        "url": {"type": "string"},
        "project_id": {"type": ["integer", "string"]},
        "file_id": {"type": ["integer", "string"]},
        "mem_min": {"type": "number", "minimum": 0.5},
        "mem_max": {"type": "number", "minimum": 0.5},
        "server_properties_port": {"type": "integer", "minimum": 1, "maximum": 65535},
    },
    "required": ["name"],
    "additionalProperties": False,
}


class ApiServersFromCurseforgeHandler(BaseApiHandler):
    """Create a brand-new server from a CurseForge modpack.

    Accepts a ``url`` (curseforge.com project page or a bare id) or an explicit
    ``project_id`` (+ optional ``file_id``). Downloads the pack zip keyless,
    parses its manifest, creates the loader server, then downloads every mod and
    applies the overrides in the background.
    """

    def post(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if EnumPermissionsCrafty.SERVER_CREATION not in auth_data[1]:
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
            validate(data, from_curseforge_schema)
        except (json.JSONDecodeError, ValidationError) as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )

        url = (data.get("url") or "").strip()
        project_id = data.get("project_id")
        file_id = data.get("file_id")
        if not url and not project_id:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "MISSING_SOURCE",
                    "error_data": "Provide a CurseForge url, or a project_id.",
                },
            )

        mgr = CurseForgeManager()

        # Resolve to (project_id, file_id) ------------------------------------
        try:
            if url:
                project_id, file_id = mgr.resolve_modpack(url)
            else:
                project_id = int(project_id)
                file_id = int(file_id) if file_id else mgr.latest_file_id(project_id)
        except ValueError as e:
            return self.finish_json(
                400, {"status": "error", "error": "RESOLVE_FAILED", "error_data": str(e)}
            )
        except Exception as e:  # noqa: BLE001
            return self.finish_json(
                502, {"status": "error", "error": "CURSEFORGE_ERROR", "error_data": str(e)}
            )

        # Download the pack zip + parse manifest ------------------------------
        temp_dir = tempfile.mkdtemp(prefix="cfpack-")
        try:
            zip_path = mgr.download_file(temp_dir, project_id, file_id)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return self.finish_json(
                502, {"status": "error", "error": "DOWNLOAD_FAILED", "error_data": str(e)}
            )
        try:
            mc_version, jar_type, manifest = modpack_installer.parse_cf_manifest(zip_path)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return self.finish_json(
                400, {"status": "error", "error": "UNSUPPORTED_MODPACK", "error_data": str(e)}
            )

        # Create the base server then install in the background ---------------
        port = int(data.get("server_properties_port", 25565))
        payload = modpack_installer.build_create_payload(
            data["name"], jar_type, mc_version,
            data.get("mem_min", 3), data.get("mem_max", 6), port,
        )
        try:
            new_server_id = self.controller.create_api_server(payload, auth_data[4]["user_id"])
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return self.finish_json(
                400, {"status": "error", "error": "CREATE_FAILED", "error_data": f"could not create base server: {e}"}
            )

        threading.Thread(
            target=modpack_installer.install_curseforge,
            args=(self.controller, new_server_id, temp_dir, str(zip_path), manifest),
            daemon=True,
            name=f"curseforge-modpack-{new_server_id}",
        ).start()

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"created server {new_server_id} from CurseForge modpack "
            f"({project_id}:{file_id})",
            new_server_id,
            self.get_remote_ip(),
        )
        return self.finish_json(
            201,
            {
                "status": "ok",
                "data": {
                    "new_server_id": new_server_id,
                    "minecraft": mc_version,
                    "loader": jar_type,
                    "mods": len(manifest.get("files", []) or []),
                },
            },
        )
