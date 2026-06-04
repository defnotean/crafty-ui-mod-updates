import logging
import os
import re

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared.tunnel_manager import tunnel_manager

logger = logging.getLogger(__name__)


class ApiServersServerRouteHandler(BaseApiHandler):
    """GET status / POST expose / DELETE unexpose a server's public bore route."""

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
        if EnumPermissionsServer.CONFIG not in self.controller.server_perms.get_permissions(mask):
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

    def _port(self, server_id):
        # 1) live stats (running servers)
        try:
            stats = self.controller.servers.get_server_stats_by_id(server_id)
            if isinstance(stats, dict):
                p = stats.get("game_port") or stats.get("server_port")
                if p:
                    return int(p)
        except Exception:
            pass
        # 2) server.properties / server_data (never-run servers)
        try:
            srv = self.controller.servers.get_server_data_by_id(server_id) or {}
            path = srv.get("path")
            if path:
                props = os.path.join(path, "server.properties")
                if os.path.isfile(props):
                    with open(props, "r", encoding="utf-8", errors="ignore") as f:
                        m = re.search(r"(?mi)^\s*server-port\s*=\s*(\d+)", f.read())
                        if m:
                            return int(m.group(1))
            for key in ("server_port", "port"):
                if srv.get(key):
                    return int(srv[key])
        except Exception:
            pass
        return None

    def get(self, server_id):
        if not self._authorize(server_id):
            return
        return self.finish_json(200, {"status": "ok", "data": tunnel_manager.status(server_id)})

    def post(self, server_id):
        auth_data = self._authorize(server_id)
        if not auth_data:
            return
        port = self._port(server_id)
        if not port:
            return self.finish_json(
                400,
                {"status": "error", "error": "NO_PORT", "error_data": "Could not determine the server's port."},
            )
        result = tunnel_manager.start(server_id, port)
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"exposed server {server_id} publicly ({result.get('address')})",
            server_id,
            self.get_remote_ip(),
        )
        return self.finish_json(200, {"status": "ok", "data": result})

    def delete(self, server_id):
        auth_data = self._authorize(server_id)
        if not auth_data:
            return
        result = tunnel_manager.stop(server_id)
        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"stopped public routing for server {server_id}",
            server_id,
            self.get_remote_ip(),
        )
        return self.finish_json(200, {"status": "ok", "data": result})
