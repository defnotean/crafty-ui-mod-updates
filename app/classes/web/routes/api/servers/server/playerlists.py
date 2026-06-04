import logging

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


class ApiServersServerPlayerlistsHandler(BaseApiHandler):
    """GET the live whitelist / ban list / known-players for a server so the
    player-management UI can refresh after an action without a full reload."""

    def get(self, server_id):
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
        if EnumPermissionsServer.PLAYERS not in self.controller.server_perms.get_permissions(
            mask
        ):
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
            whitelist = self.controller.servers.get_whitelist(server_id) or []
            banned = self.controller.servers.get_banned_players(server_id)
            if not isinstance(banned, list):
                banned = []
            enabled = self.controller.servers.get_whitelist_enabled(server_id)
            srv = self.controller.servers.get_server_instance_by_id(server_id)
            cached = srv.player_cache or []
            running = bool(srv.check_running())
        except Exception as e:  # noqa: BLE001
            return self.finish_json(
                500, {"status": "error", "error": "READ_FAILED", "error_data": str(e)}
            )

        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": {
                    "whitelist": whitelist,
                    "banned": banned,
                    "cached": cached,
                    "whitelist_enabled": enabled,
                    "running": running,
                },
            },
        )
