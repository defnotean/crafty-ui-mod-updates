"""Modrinth content client for the Crafty content hub.

A thin, defensive wrapper over the Modrinth v2 API plus a generalized,
sha512-verified, HTTPS-only downloader. Mirrors the conventions already used by
``mod_update_manager.py`` (requests.Session, traversal guards via Helpers).
"""

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path

import requests

from app.classes.helpers.helpers import Helpers

logger = logging.getLogger(__name__)


class ModrinthManager:
    API = "https://api.modrinth.com/v2"
    HEADERS = {
        "User-Agent": "CraftyController/4 (content hub)",
        "Accept": "application/json",
    }
    PROJECT_TYPES = ("modpack", "mod", "datapack", "resourcepack", "shader")

    def __init__(self, session=None):
        self.session = session or requests.Session()

    # ------------------------------------------------------------------ read
    def search(
        self,
        query="",
        project_type=None,
        loaders=None,
        game_versions=None,
        index="relevance",
        limit=20,
        offset=0,
    ):
        facets = []
        if project_type:
            facets.append([f"project_type:{project_type}"])
        if loaders:
            facets.append([f"categories:{loader}" for loader in loaders])
        if game_versions:
            facets.append([f"versions:{ver}" for ver in game_versions])
        params = {
            "limit": max(1, min(int(limit), 50)),
            "offset": max(0, int(offset)),
            "index": index,
        }
        if query:
            params["query"] = query
        if facets:
            params["facets"] = json.dumps(facets)
        return self._get("/search", params)

    def project(self, project_id):
        return self._get(f"/project/{project_id}")

    def versions(self, project_id, loaders=None, game_versions=None):
        params = {}
        if loaders:
            params["loaders"] = json.dumps(list(loaders))
        if game_versions:
            params["game_versions"] = json.dumps(list(game_versions))
        return self._get(f"/project/{project_id}/version", params)

    def version(self, version_id):
        return self._get(f"/version/{version_id}")

    @staticmethod
    def is_direct_mrpack(url):
        """True if ``url`` is a direct HTTPS link to a .mrpack file."""
        u = (url or "").strip().lower()
        return u.startswith("https://") and u.split("?")[0].endswith(".mrpack")

    def resolve_modpack(self, url):
        """Resolve a Modrinth project URL/slug (optionally pinned to a version)
        to ``(project_id, version_id)``. Picks the newest version when none is
        pinned. Raises ValueError if it can't be resolved."""
        text = (url or "").strip()
        slug = None
        version_ref = None
        page = re.search(
            r"modrinth\.com/[a-z]+/([^/?#]+)(?:/version/([^/?#]+))?", text
        )
        if page:
            slug = page.group(1)
            version_ref = page.group(2)
        elif re.fullmatch(r"[A-Za-z0-9!@$()`.+,_\"~-]+", text):
            slug = text  # bare slug or project id
        if not slug:
            raise ValueError("Not a recognizable Modrinth project URL or slug.")

        project = self.project(slug)
        project_id = project.get("id")
        if not project_id:
            raise ValueError("Modrinth project not found.")
        versions = self.versions(project_id) or []
        if not versions:
            raise ValueError("This Modrinth project has no published versions.")

        chosen = None
        if version_ref:
            chosen = next(
                (
                    v
                    for v in versions
                    if v.get("id") == version_ref
                    or v.get("version_number") == version_ref
                ),
                None,
            )
        if not chosen:
            # newest by publish date
            chosen = sorted(
                versions, key=lambda v: v.get("date_published", ""), reverse=True
            )[0]
        return project_id, chosen.get("id")

    def _get(self, endpoint, params=None):
        response = self.session.get(
            f"{self.API}{endpoint}",
            params=params or {},
            headers=self.HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------- download
    @staticmethod
    def primary_file(version):
        files = (version or {}).get("files") or []
        if not files:
            return {}
        return next((f for f in files if f.get("primary")), files[0])

    @staticmethod
    def hash_file(path, algorithm="sha512"):
        digest = hashlib.new(algorithm)
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def download_to(self, target_dir, url, expected_sha512=None, filename=None):
        """Download ``url`` into ``target_dir`` (HTTPS-only, sha512-verified,
        traversal-guarded). Returns the final Path."""
        target_dir = Path(target_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        if not str(url).lower().startswith("https://"):
            raise ValueError("Refusing non-HTTPS download URL")
        name = filename or os.path.basename(str(url).split("?")[0]) or "download.bin"
        target = (target_dir / name).resolve()
        if not Helpers.is_subdir(str(target), str(target_dir)):
            raise ValueError("Resolved download path escapes the target directory")

        with tempfile.NamedTemporaryFile(
            delete=False, dir=str(target_dir), suffix=".part"
        ) as tmp:
            tmp_path = Path(tmp.name)
            try:
                with self.session.get(
                    url, headers=self.HEADERS, stream=True, timeout=180
                ) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            tmp.write(chunk)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

        if expected_sha512:
            actual = self.hash_file(tmp_path, "sha512")
            if actual != expected_sha512:
                tmp_path.unlink(missing_ok=True)
                raise ValueError("Downloaded file hash did not match Modrinth metadata")
        os.replace(tmp_path, target)
        return target
