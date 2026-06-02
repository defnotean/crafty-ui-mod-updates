import json
import logging

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.shared.mod_update_manager import ModUpdateManager
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


class ApiServersServerModsHandler(BaseApiHandler):
    def get(self, server_id: str):
        auth_data = self._authorize_files(server_id)
        if not auth_data:
            return

        loader = self.get_argument("loader", None)
        game_version = self.get_argument("game_version", None)
        server_data = self.controller.servers.get_server_data_by_id(server_id)
        manager = ModUpdateManager(server_data["path"])

        if not loader:
            loader = ModUpdateManager.infer_loader(server_data)
        if not game_version:
            game_version = self._infer_game_version(server_id, server_data)

        try:
            return self.finish_json(
                200,
                {
                    "status": "ok",
                    "data": manager.scan(loader, game_version),
                },
            )
        except ValueError as exc:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_MODS_DIRECTORY",
                    "error_data": str(exc),
                },
            )

    def post(self, server_id: str):
        auth_data = self._authorize_files(server_id)
        if not auth_data:
            return

        server_obj = self.controller.servers.get_server_obj_optional(server_id)
        if server_obj and server_obj.check_running():
            return self.finish_json(
                409,
                {
                    "status": "error",
                    "error": "SERVER_RUNNING",
                    "error_data": "Stop the server before applying mod updates.",
                },
            )

        try:
            data = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON",
                    "error_data": "Request body must be valid JSON.",
                },
            )

        server_data = self.controller.servers.get_server_data_by_id(server_id)
        loader = data.get("loader") or ModUpdateManager.infer_loader(server_data)
        game_version = data.get("game_version") or self._infer_game_version(
            server_id, server_data
        )
        manager = ModUpdateManager(server_data["path"])

        try:
            update_result = manager.update_available(loader, game_version)
        except ValueError as exc:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_MODS_DIRECTORY",
                    "error_data": str(exc),
                },
            )

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            "updated installed mods",
            server_id,
            self.get_remote_ip(),
        )

        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": update_result,
            },
        )

    def _authorize_files(self, server_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return None

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
        server_permissions = self.controller.server_perms.get_permissions(mask)
        if EnumPermissionsServer.FILES not in server_permissions:
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

        return auth_data

    def _infer_game_version(self, server_id: str, server_data: dict) -> str:
        server_stats = {}
        try:
            server_stats = self.controller.servers.get_server_stats_by_id(server_id)
        except ValueError:
            logger.debug("Server stats unavailable while inferring mod game version")
        return ModUpdateManager.infer_game_version(server_stats, server_data)
