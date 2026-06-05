import logging
import os
import shutil
import tempfile
import threading

from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared import modpack_installer

logger = logging.getLogger(__name__)


class ApiServersImportPackHandler(BaseApiHandler):
    """Create a server from an UPLOADED modpack file.

    Accepts a multipart form with ``file`` (a Modrinth .mrpack or a CurseForge
    modpack .zip) plus ``name`` / ``mem_min`` / ``mem_max`` /
    ``server_properties_port`` fields. The pack type is auto-detected.
    """

    def _deny(self, lang):
        return self.finish_json(
            400,
            {
                "status": "error",
                "error": "NOT_AUTHORIZED",
                "error_data": self.helper.translation.translate(
                    "validators", "insufficientPerms", lang
                ),
            },
        )

    def post(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if EnumPermissionsCrafty.SERVER_CREATION not in auth_data[1]:
            return self._deny(auth_data[4]["lang"])

        files = self.request.files.get("file") or []
        if not files:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NO_FILE",
                    "error_data": "No modpack file was uploaded.",
                },
            )
        upload = files[0]
        body = upload.get("body") or b""
        orig_name = upload.get("filename") or "modpack"
        if not body:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "EMPTY_FILE",
                    "error_data": "The uploaded file is empty.",
                },
            )

        def arg(name, default=None):
            try:
                return self.get_body_argument(name, default)
            except Exception:
                return default

        server_name = (arg("name") or "").strip()
        if len(server_name) < 2:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "BAD_NAME",
                    "error_data": "Provide a server name (2+ characters).",
                },
            )
        try:
            port = int(arg("server_properties_port", arg("port", 25565)))
        except (TypeError, ValueError):
            port = 25565

        def fnum(name, default):
            try:
                return float(arg(name, default))
            except (TypeError, ValueError):
                return default

        mem_min = fnum("mem_min", 2)
        mem_max = fnum("mem_max", 4)

        # Persist upload to a temp file --------------------------------------
        temp_dir = tempfile.mkdtemp(prefix="packup-")
        pack_path = os.path.join(temp_dir, "upload.pack")
        try:
            with open(pack_path, "wb") as handle:
                handle.write(body)
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(temp_dir, ignore_errors=True)
            return self.finish_json(
                500, {"status": "error", "error": "WRITE_FAILED", "error_data": str(e)}
            )

        kind = modpack_installer.detect_pack_type(pack_path)
        index = manifest = None
        try:
            if kind == "modrinth":
                mc_version, jar_type, index = modpack_installer.parse_mrpack(pack_path)
            elif kind == "curseforge":
                mc_version, jar_type, manifest = modpack_installer.parse_cf_manifest(
                    pack_path
                )
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "UNKNOWN_PACK",
                        "error_data": "File is not a Modrinth (.mrpack) or CurseForge modpack archive.",
                    },
                )
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

        payload = modpack_installer.build_create_payload(
            server_name, jar_type, mc_version, mem_min, mem_max, port
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

        if kind == "modrinth":
            threading.Thread(
                target=modpack_installer.install_modrinth,
                args=(self.controller, new_server_id, temp_dir, pack_path, index),
                daemon=True,
                name=f"modrinth-upload-{new_server_id}",
            ).start()
        else:
            threading.Thread(
                target=modpack_installer.install_curseforge,
                args=(self.controller, new_server_id, temp_dir, pack_path, manifest),
                daemon=True,
                name=f"curseforge-upload-{new_server_id}",
            ).start()

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"created server {new_server_id} from uploaded {kind} modpack '{orig_name}'",
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
                    "source": kind,
                },
            },
        )
