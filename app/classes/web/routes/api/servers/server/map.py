import logging
import os
import re

from tornado.httpclient import AsyncHTTPClient, HTTPRequest

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

# Response headers we forward from BlueMap back to the browser.
_PASS_HEADERS = (
    "Content-Type",
    "Cache-Control",
    "ETag",
    "Last-Modified",
    "Expires",
    "Content-Encoding",
    "Content-Disposition",
    "Accept-Ranges",
)


def read_bluemap_port(server_path):
    """Read the BlueMap integrated web server port from its config. Returns the
    port int, or None if BlueMap is not set up yet for this server."""
    if not server_path:
        return None
    conf = os.path.join(server_path, "plugins", "BlueMap", "webserver.conf")
    try:
        if os.path.isfile(conf):
            with open(conf, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            match = re.search(r"(?mi)^\s*port\s*[:=]\s*(\d+)", text)
            if match:
                return int(match.group(1))
            # BlueMap defaults to 8100 if the key is absent/commented.
            return 8100
    except Exception as e:
        logger.warning("could not read BlueMap port: %s", e)
    return None


class ApiServersServerMapHandler(BaseApiHandler):
    """Auth-gated reverse proxy to a server's LOCAL BlueMap web server, so the
    live 3D map + player positions stay behind the Crafty login and are never
    exposed publicly. The browser (iframe) sends the session cookie, which
    authenticate_user() honors."""

    async def get(self, server_id, rest):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            self.set_status(403)
            self.finish("Forbidden")
            return
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        if EnumPermissionsServer.LOGS not in self.controller.server_perms.get_permissions(
            mask
        ):
            self.set_status(403)
            self.finish("Forbidden")
            return

        srv = self.controller.servers.get_server_data_by_id(server_id) or {}
        port = read_bluemap_port(srv.get("path"))
        if not port:
            self.set_status(503)
            self.set_header("Content-Type", "text/plain; charset=utf-8")
            self.finish(
                "BlueMap is still setting up for this server. "
                "The live map appears here once the first render finishes."
            )
            return

        rest = rest or ""
        target = f"http://127.0.0.1:{port}/{rest}"
        if self.request.query:
            target += "?" + self.request.query

        client = AsyncHTTPClient()
        try:
            resp = await client.fetch(
                HTTPRequest(
                    target,
                    method="GET",
                    request_timeout=60,
                    connect_timeout=10,
                    follow_redirects=False,
                ),
                raise_error=False,
            )
        except Exception as e:
            self.set_status(502)
            self.set_header("Content-Type", "text/plain; charset=utf-8")
            self.finish(f"Map upstream error: {e}")
            return

        self.set_status(resp.code if resp.code and 100 <= resp.code < 600 else 502)
        for header in _PASS_HEADERS:
            value = resp.headers.get(header)
            if value:
                self.set_header(header, value)
        if resp.body:
            self.write(resp.body)
        self.finish()
