import logging
import socket

import psutil

from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


def _round_to_half_gb_mb(mb):
    """Round a MB value to the nearest 0.5 GB (512 MB), floored at 512 MB."""
    step = 512
    return max(step, int(round(mb / step)) * step)


class ApiCraftyHostInfoHandler(BaseApiHandler):
    """GET host hardware info + recommended server RAM for the create wizard.

    The recommendation reserves headroom for the OS and the panel itself, then
    hands a sensible chunk of what is left to a single server (capped so we never
    suggest absurd heap sizes on big boxes). Values are returned in both MB and
    GB; the wizard form works in GB.
    """

    def get(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        try:
            vm = psutil.virtual_memory()
            total_mb = vm.total / (1024 * 1024)
            avail_mb = vm.available / (1024 * 1024)
        except Exception as e:
            logger.error(f"host-info: psutil memory read failed: {e}")
            return self.finish_json(
                500,
                {
                    "status": "error",
                    "error": "HOST_INFO_FAILED",
                    "error_data": str(e),
                },
            )

        try:
            cpu_cores = psutil.cpu_count(logical=True) or 1
        except Exception:
            cpu_cores = 1

        # Reserve headroom for the OS + panel, give ~60% of the rest to one
        # server, cap at 8 GB (Minecraft rarely benefits past that for vanilla).
        reserve_mb = min(4096, max(2048, total_mb * 0.25))
        usable_mb = max(1024, total_mb - reserve_mb)
        rec_max_mb = min(8192, max(1024, usable_mb * 0.6))
        rec_max_mb = _round_to_half_gb_mb(rec_max_mb)
        rec_min_mb = min(4096, max(1024, rec_max_mb / 2))
        rec_min_mb = _round_to_half_gb_mb(rec_min_mb)
        if rec_min_mb > rec_max_mb:
            rec_min_mb = rec_max_mb

        self.finish_json(
            200,
            {
                "status": "ok",
                "data": {
                    "mem_total_mb": int(total_mb),
                    "mem_total_gb": round(total_mb / 1024, 1),
                    "mem_available_mb": int(avail_mb),
                    "mem_available_gb": round(avail_mb / 1024, 1),
                    "cpu_cores": cpu_cores,
                    "recommended": {
                        "min_mb": int(rec_min_mb),
                        "max_mb": int(rec_max_mb),
                        "min_gb": round(rec_min_mb / 1024, 1),
                        "max_gb": round(rec_max_mb / 1024, 1),
                    },
                },
            },
        )


class ApiCraftyPortCheckHandler(BaseApiHandler):
    """GET whether a TCP port is free to use on this host.

    Reports two kinds of conflict:
      * crafty_server - another Crafty-managed server is already configured on it
      * host_bound    - something is currently listening/bound on the port
    """

    def get(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        raw = self.get_argument("port", None)
        try:
            port = int(raw)
        except (TypeError, ValueError):
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_PORT",
                    "error_data": "Port must be a number.",
                },
            )
        if port < 1 or port > 65535:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_PORT",
                    "error_data": "Port must be between 1 and 65535.",
                },
            )

        # Optionally ignore a server's own port (useful when editing later).
        ignore_id = self.get_argument("ignore", None)
        # Protocol for the host-bound probe: tcp (Java/Hytale) or udp (Bedrock).
        proto = (self.get_argument("proto", "tcp") or "tcp").lower()
        if proto not in ("tcp", "udp"):
            proto = "tcp"

        conflicts = []

        # 1) Another Crafty-managed server already configured on this port?
        try:
            for srv in self.controller.servers.get_all_defined_servers():
                if ignore_id and str(srv.get("server_id")) == str(ignore_id):
                    continue
                try:
                    sp = int(srv.get("server_port"))
                except (TypeError, ValueError):
                    continue
                if sp == port:
                    conflicts.append(
                        {
                            "type": "crafty_server",
                            "server_id": str(srv.get("server_id")),
                            "server_name": srv.get("server_name") or "Unknown",
                        }
                    )
        except Exception as e:
            logger.warning(f"port-check: server enumeration failed: {e}")

        # 2) Is the port currently bound/listening on this host?
        host_bound = False
        sock = None
        try:
            sock_type = socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM
            sock = socket.socket(socket.AF_INET, sock_type)
            # On Windows, an exclusive bind fails if anything else already holds
            # the port (needed to detect held UDP ports for Bedrock).
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                except OSError:
                    pass
            sock.settimeout(1)
            sock.bind(("0.0.0.0", port))
        except OSError:
            host_bound = True
        finally:
            try:
                if sock is not None:
                    sock.close()
            except Exception:
                pass
        if host_bound:
            conflicts.append({"type": "host_bound"})

        self.finish_json(
            200,
            {
                "status": "ok",
                "data": {
                    "port": port,
                    "proto": proto,
                    "available": len(conflicts) == 0,
                    "conflicts": conflicts,
                },
            },
        )
