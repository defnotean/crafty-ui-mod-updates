import json
import logging
import shutil
import tempfile
import threading

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared.modrinth_manager import ModrinthManager
from app.classes.shared import modpack_installer

logger = logging.getLogger(__name__)

from_modrinth_schema = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 2, "pattern": r"^[^/\\#]*$"},
        "url": {"type": "string"},
        "project_id": {"type": "string", "minLength": 1},
        "version_id": {"type": "string", "minLength": 1},
        "mem_min": {"type": "number", "minimum": 0.5},
        "mem_max": {"type": "number", "minimum": 0.5},
        "server_properties_port": {"type": "integer", "minimum": 1, "maximum": 65535},
    },
    "required": ["name"],
    "additionalProperties": False,
}


class ApiServersFromModrinthHandler(BaseApiHandler):
    """Create a brand-new server from a Modrinth modpack.

    Accepts either a Discover-style ``project_id`` + ``version_id``, OR a ``url``
    (a modrinth.com project/version page, a bare slug/id, or a direct .mrpack link).
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
            validate(data, from_modrinth_schema)
        except (json.JSONDecodeError, ValidationError) as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )

        url = (data.get("url") or "").strip()
        project_id = data.get("project_id")
        version_id = data.get("version_id")
        if not url and not (project_id and version_id):
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "MISSING_SOURCE",
                    "error_data": "Provide either a Modrinth url, or a project_id and version_id.",
                },
            )

        mgr = ModrinthManager()

        # Resolve the .mrpack download URL (+ optional sha512) ------------------
        dl_url = None
        sha512 = None
        try:
            if url and ModrinthManager.is_direct_mrpack(url):
                dl_url = url  # paste-a-link straight to a .mrpack
            else:
                if url and not (project_id and version_id):
                    project_id, version_id = mgr.resolve_modpack(url)
                version = mgr.version(version_id)
                pfile = ModrinthManager.primary_file(version)
                dl_url = pfile.get("url")
                sha512 = (pfile.get("hashes") or {}).get("sha512")
        except ValueError as e:
            return self.finish_json(
                400,
                {"status": "error", "error": "RESOLVE_FAILED", "error_data": str(e)},
            )
        except Exception as e:  # noqa: BLE001
            return self.finish_json(
                502,
                {"status": "error", "error": "MODRINTH_ERROR", "error_data": str(e)},
            )
        if not dl_url:
            return self.finish_json(
                404,
                {
                    "status": "error",
                    "error": "NO_FILE",
                    "error_data": "Modpack version has no downloadable file.",
                },
            )

        # Download + read the pack index --------------------------------------
        temp_dir = tempfile.mkdtemp(prefix="mrpack-")
        try:
            mrpack_path = mgr.download_to(temp_dir, dl_url, sha512, "modpack.mrpack")
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return self.finish_json(
                502,
                {"status": "error", "error": "DOWNLOAD_FAILED", "error_data": str(e)},
            )
        try:
            mc_version, jar_type, index = modpack_installer.parse_mrpack(mrpack_path)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "UNSUPPORTED_MODPACK",
                    "error_data": str(e),
                },
            )

        # Create the base server then install in the background ---------------
        port = int(data.get("server_properties_port", 25565))
        payload = modpack_installer.build_create_payload(
            data["name"],
            jar_type,
            mc_version,
            data.get("mem_min", 2),
            data.get("mem_max", 4),
            port,
        )
        try:
            new_server_id = self.controller.create_api_server(
                payload, auth_data[4]["user_id"]
            )
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "CREATE_FAILED",
                    "error_data": f"could not create base server: {e}",
                },
            )

        threading.Thread(
            target=modpack_installer.install_modrinth,
            args=(self.controller, new_server_id, temp_dir, mrpack_path, index),
            daemon=True,
            name=f"modrinth-modpack-{new_server_id}",
        ).start()

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"created server {new_server_id} from Modrinth modpack "
            f"({project_id or url})",
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
                },
            },
        )
