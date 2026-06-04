import json
import logging
from datetime import datetime, timezone

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared import mod_autoupdate

logger = logging.getLogger(__name__)


class ApiServersServerModsAutoupdateHandler(BaseApiHandler):
    """GET / POST a server's scheduled mod auto-update settings."""

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

    def _path(self, server_id):
        data = self.controller.servers.get_server_data_by_id(server_id) or {}
        return data.get("path")

    def get(self, server_id):
        if not self._authorize(server_id):
            return
        path = self._path(server_id)
        if not path:
            return self.finish_json(
                400, {"status": "error", "error": "NO_PATH", "error_data": "server path unknown"}
            )
        return self.finish_json(
            200, {"status": "ok", "data": mod_autoupdate.get_config(path)}
        )

    def post(self, server_id):
        auth_data = self._authorize(server_id)
        if not auth_data:
            return
        path = self._path(server_id)
        if not path:
            return self.finish_json(
                400, {"status": "error", "error": "NO_PATH", "error_data": "server path unknown"}
            )
        try:
            data = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": "bad JSON"}
            )
        enabled = bool(data.get("enabled"))
        update_minecraft = bool(data.get("update_minecraft"))
        frequency = data.get("frequency", "weekly")
        # Reset the clock on every save so enabling starts the schedule from now
        # (no surprise immediate update/restart).
        cfg = mod_autoupdate.set_config(
            path,
            enabled,
            frequency,
            datetime.now(timezone.utc).isoformat(),
            update_minecraft,
        )
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"set auto-update mods={cfg['enabled']} minecraft={cfg['update_minecraft']} "
            f"frequency={cfg['frequency']}",
            server_id,
            self.get_remote_ip(),
        )
        return self.finish_json(200, {"status": "ok", "data": cfg})
