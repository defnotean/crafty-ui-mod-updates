"""Per-server public routing via bore.pub.

Spawns/tracks one bore tunnel per server (reusing the same node bore client the
host already uses for the live server), so any server can be exposed to the
internet on a ``bore.pub:<port>`` address with one click — no manual scripts.
"""

import logging
import os
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# Host-specific tooling (same as run-bore-minecraft.ps1). Kept as module
# constants so they're trivial to change if the layout moves.
NODE = r"C:\Program Files\nodejs\node.exe"
BORE_CLIENT = r"C:\Users\Administrator\Desktop\Minecraft\runtime\bore\bore-client.mjs"
BORE_HOST = "bore.pub"
_ADDR_RE = re.compile(r"bore\.pub[:\s]+(\d{2,5})", re.IGNORECASE)


class _Tunnel:
    __slots__ = ("server_id", "port", "proc", "address", "error", "started")

    def __init__(self, server_id, port):
        self.server_id = server_id
        self.port = port
        self.proc = None
        self.address = None
        self.error = None
        self.started = time.time()


class TunnelManager:
    def __init__(self):
        self._tunnels = {}
        self._lock = threading.Lock()

    def _alive(self, tunnel):
        return bool(tunnel and tunnel.proc and tunnel.proc.poll() is None)

    def status(self, server_id):
        tunnel = self._tunnels.get(server_id)
        if not tunnel:
            return {"exposed": False, "address": None}
        return {
            "exposed": self._alive(tunnel),
            "address": tunnel.address,
            "port": tunnel.port,
            "error": tunnel.error,
        }

    def _spawn(self, tunnel, remote_port):
        """Spawn a bore process for tunnel.port -> bore.pub:remote_port
        (remote_port 0 = let bore auto-assign a free port). No console window."""
        tunnel.address = None
        tunnel.error = None
        tunnel.proc = subprocess.Popen(
            [
                NODE,
                BORE_CLIENT,
                "--local-port",
                str(tunnel.port),
                "--local-host",
                "127.0.0.1",
                "--to",
                BORE_HOST,
                "--port",
                str(remote_port if remote_port else 0),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(
            target=self._reader,
            args=(tunnel,),
            daemon=True,
            name=f"bore-{tunnel.server_id}",
        ).start()

    def _await(self, tunnel):
        # give bore a moment to report its assigned address (or to die)
        for _ in range(40):
            if tunnel.address or tunnel.proc.poll() is not None:
                break
            time.sleep(0.3)

    def start(self, server_id, port):
        if not port:
            return {"exposed": False, "error": "no server port"}
        if not os.path.isfile(BORE_CLIENT):
            return {
                "exposed": False,
                "error": f"bore client not found at {BORE_CLIENT}",
            }
        with self._lock:
            existing = self._tunnels.get(server_id)
            if self._alive(existing):
                return self.status(server_id)
            tunnel = _Tunnel(server_id, port)
            self._tunnels[server_id] = tunnel
            try:
                # Prefer a memorable public port matching the server's port…
                self._spawn(tunnel, port)
            except Exception as e:  # noqa: BLE001
                tunnel.error = str(e)
                logger.warning("Failed to start bore tunnel for %s: %s", server_id, e)
                return {"exposed": False, "error": str(e)}
        self._await(tunnel)

        # …but if that public port is already taken (e.g. the server is already
        # exposed elsewhere on its canonical port, like play.defnotean.ca on
        # 25565), bore exits with "port already in use" and no address — so fall
        # back to an auto-assigned port so expose always yields a working link.
        if not tunnel.address:
            logger.info(
                "bore: public port %s unavailable for %s; retrying with an "
                "auto-assigned port",
                port,
                server_id,
            )
            try:
                with self._lock:
                    self._spawn(tunnel, 0)
                self._await(tunnel)
            except Exception as e:  # noqa: BLE001
                tunnel.error = str(e)
        return self.status(server_id)

    def _reader(self, tunnel):
        try:
            for line in tunnel.proc.stdout:
                if not line:
                    continue
                match = _ADDR_RE.search(line)
                if match:
                    tunnel.address = f"bore.pub:{match.group(1)}"
                elif not tunnel.address and re.search(
                    r"(?i)error|refused|failed|denied|in use|already|unavailable",
                    line,
                ):
                    tunnel.error = line.strip()[:200]
        except Exception as e:  # noqa: BLE001
            logger.debug("bore reader for %s ended: %s", tunnel.server_id, e)

    def stop(self, server_id):
        with self._lock:
            tunnel = self._tunnels.pop(server_id, None)
        if tunnel and tunnel.proc:
            try:
                tunnel.proc.terminate()
            except Exception:
                pass
        return {"exposed": False, "address": None}


# process-wide singleton
tunnel_manager = TunnelManager()
