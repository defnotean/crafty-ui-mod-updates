import logging
import re

import requests

from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared.tunnel_manager import tunnel_manager

logger = logging.getLogger(__name__)

# Public DNS-over-HTTPS resolver — no credentials, no Cloudflare account needed.
DOH_URL = "https://cloudflare-dns.com/dns-query"
HOSTNAME_RE = re.compile(r"^(?=.{3,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


class ApiServersServerDomainHandler(BaseApiHandler):
    """Verify that a user's custom domain has the right Minecraft SRV record
    pointing at this server's public (bore) address. No DNS write access needed."""

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

        hostname = (self.get_query_argument("hostname", "") or "").strip().lower().rstrip(".")
        if not hostname or not HOSTNAME_RE.match(hostname):
            return self.finish_json(
                400,
                {"status": "error", "error": "BAD_HOSTNAME", "error_data": "Enter a valid domain, e.g. play.example.com"},
            )

        # what the SRV should point at, from this server's current tunnel
        status = tunnel_manager.status(server_id)
        expected_port = None
        if status.get("address"):
            try:
                expected_port = int(str(status["address"]).rsplit(":", 1)[-1])
            except Exception:
                expected_port = None

        name = f"_minecraft._tcp.{hostname}"
        srv = []
        try:
            resp = requests.get(
                DOH_URL,
                params={"name": name, "type": "SRV"},
                headers={"Accept": "application/dns-json"},
                timeout=8,
            )
            resp.raise_for_status()
            for answer in (resp.json().get("Answer") or []):
                if answer.get("type") == 33:  # SRV
                    parts = str(answer.get("data", "")).split()
                    if len(parts) >= 4:
                        srv.append(
                            {
                                "priority": parts[0],
                                "weight": parts[1],
                                "port": int(parts[2]),
                                "target": parts[3].rstrip(".").lower(),
                            }
                        )
        except Exception as e:  # noqa: BLE001
            return self.finish_json(
                502, {"status": "error", "error": "DNS_ERROR", "error_data": str(e)}
            )

        matches = any(
            s["target"] == "bore.pub" and (expected_port is None or s["port"] == expected_port)
            for s in srv
        )
        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": {
                    "hostname": hostname,
                    "record_name": name,
                    "resolved": bool(srv),
                    "srv": srv,
                    "expected_target": "bore.pub",
                    "expected_port": expected_port,
                    "matches": matches,
                },
            },
        )
