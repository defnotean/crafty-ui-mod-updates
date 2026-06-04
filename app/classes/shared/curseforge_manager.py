"""CurseForge modpack client (keyless).

CurseForge's official API needs an API key, but two public, key-free paths let us
fully automate modpack import:

  * cfwidget.com  - proxies project metadata (slug -> projectID, latest fileID)
  * curseforge.com/api/v1/mods/{id}/files/{fid}/download - the website's own
    download route, which 307-redirects to the Forge CDN with the real filename
    in the path.

If ``CURSEFORGE_API_KEY`` is set in the environment we add it as ``x-api-key``
for richer/faster metadata, but it is never required.
"""

import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote

import requests

from app.classes.helpers.helpers import Helpers

logger = logging.getLogger(__name__)


class CurseForgeManager:
    WIDGET = "https://api.cfwidget.com"
    WEBSITE = "https://www.curseforge.com/api/v1"
    UA = "CraftyController/4 (modpack import)"

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.api_key = os.environ.get("CURSEFORGE_API_KEY") or os.environ.get(
            "CF_API_KEY"
        )

    # ------------------------------------------------------------ resolve URL
    def resolve_modpack(self, url):
        """Resolve a CurseForge URL/slug/id to (project_id, file_id)."""
        url = (url or "").strip()

        # bare ids: "715572" or "715572:7097953"
        ids = re.fullmatch(r"(\d+)(?::(\d+))?", url)
        if ids:
            pid = int(ids.group(1))
            fid = int(ids.group(2)) if ids.group(2) else self.latest_file_id(pid)
            return pid, fid

        # curseforge.com/minecraft/modpacks/<slug>[/files/<id>] (or /download/<id>)
        slug_m = re.search(r"curseforge\.com/minecraft/modpacks/([A-Za-z0-9._-]+)", url)
        file_m = re.search(r"/(?:files|download)/(\d+)", url)
        if slug_m:
            slug = slug_m.group(1)
            info = self._widget(f"/minecraft/modpacks/{slug}")
            pid = info.get("id")
            if file_m:
                fid = int(file_m.group(1))
            else:
                fid = (info.get("download") or {}).get("id")
                if not fid:
                    files = info.get("files") or []
                    fid = files[0].get("id") if files else None
            if not pid or not fid:
                raise ValueError(
                    "Could not resolve this CurseForge modpack (no downloadable file found)."
                )
            return int(pid), int(fid)

        raise ValueError(
            "Not a recognizable CurseForge modpack URL. Paste the project page URL, "
            "e.g. https://www.curseforge.com/minecraft/modpacks/<name>"
        )

    def latest_file_id(self, project_id):
        info = self._widget(f"/{int(project_id)}")
        fid = (info.get("download") or {}).get("id")
        if not fid:
            files = info.get("files") or []
            fid = files[0].get("id") if files else None
        if not fid:
            raise ValueError("No downloadable file found for this CurseForge project.")
        return int(fid)

    def _widget(self, path):
        # cfwidget returns 202 while it queues a fresh project; retry briefly.
        headers = {"User-Agent": self.UA, "Accept": "application/json"}
        last = None
        for _ in range(6):
            resp = self.session.get(
                f"{self.WIDGET}{path}", headers=headers, timeout=25
            )
            if resp.status_code == 202:
                last = resp
                time.sleep(2)
                continue
            resp.raise_for_status()
            return resp.json()
        if last is not None:
            last.raise_for_status()
        raise ValueError("CurseForge metadata service timed out.")

    # -------------------------------------------------------------- download
    def _download_headers(self):
        headers = {"User-Agent": self.UA}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def download_file(self, target_dir, project_id, file_id, subdir=None, filename=None):
        """Download a CurseForge file into ``target_dir`` (optionally a subdir).

        Uses the keyless website download route, follows the CDN redirect, and
        derives the filename from the final URL. Traversal-guarded. Returns Path.
        """
        base = Path(target_dir)
        if subdir:
            base = base / subdir
        base = base.resolve()
        base.mkdir(parents=True, exist_ok=True)

        url = f"{self.WEBSITE}/mods/{int(project_id)}/files/{int(file_id)}/download"
        with self.session.get(
            url,
            headers=self._download_headers(),
            stream=True,
            timeout=180,
            allow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            final = unquote(str(resp.url).split("?")[0])
            name = filename or os.path.basename(final) or f"{file_id}.jar"
            target = (base / name).resolve()
            if not Helpers.is_subdir(str(target), str(base)):
                raise ValueError("Resolved download path escapes the target directory")
            tmp = Path(str(target) + ".part")
            try:
                with open(tmp, "wb") as handle:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                os.replace(tmp, target)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
            return target
