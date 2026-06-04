import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import requests

from app.classes.helpers.helpers import Helpers

logger = logging.getLogger(__name__)


class ModUpdateManager:
    MODRINTH_API = "https://api.modrinth.com/v2"
    MODRINTH_HEADERS = {
        "User-Agent": "CraftyController/4 (mod update manager)",
        "Accept": "application/json",
    }
    SUPPORTED_LOADERS = ("fabric", "forge", "neoforge", "quilt")

    def __init__(self, server_path: str | Path, session=None):
        self.server_path = Path(server_path).resolve()
        self.mods_path = self.server_path / "mods"
        self.session = session or requests.Session()

    @staticmethod
    def infer_loader(server_data: dict[str, Any] | None = None) -> str:
        server_data = server_data or {}
        haystack = " ".join(
            str(server_data.get(key, ""))
            for key in (
                "executable",
                "execution_command",
                "executable_update_url",
                "server_name",
            )
        ).lower()
        for loader in ModUpdateManager.SUPPORTED_LOADERS:
            if loader in haystack:
                return loader
        return "fabric"

    @staticmethod
    def infer_game_version(
        server_stats: dict[str, Any] | None = None,
        server_data: dict[str, Any] | None = None,
    ) -> str:
        server_stats = server_stats or {}
        server_data = server_data or {}
        # The bigbucket download URL embeds the version as /<type>/<version>/<file>
        # — the most reliable source, and it handles calendar versions (e.g. 26.1.2)
        # that the old "1.x"-only regex mis-read (it pulled "1.2" out of "26.1.2").
        url = str(server_data.get("executable_update_url") or "")
        match = re.search(r"/[^/]+/(\d+\.\d+(?:\.\d+)?)/[^/]+$", url)
        if match:
            return match.group(1)
        for value in (
            server_stats.get("version"),
            server_data.get("executable_update_url"),
            server_data.get("executable"),
            server_data.get("execution_command"),
        ):
            if not value:
                continue
            match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", str(value))
            if match:
                return match.group(1)
        return ""

    def scan(self, loader: str | None = None, game_version: str | None = None):
        loader = self._clean_loader(loader)
        game_versions = self._clean_game_versions(game_version)
        jars = self._installed_jars()
        mods = [self._local_mod_entry(path) for path in jars]
        hashes = [mod["sha512"] for mod in mods]

        current_versions = self._get_modrinth_versions(hashes) if hashes else {}
        latest_versions = {}
        check_latest = bool(hashes and loader and game_versions)
        if check_latest:
            latest_versions = self._get_modrinth_latest_versions(
                hashes, loader, game_versions
            )

        for mod in mods:
            current = current_versions.get(mod["sha512"])
            latest = latest_versions.get(mod["sha512"])
            self._merge_modrinth_data(mod, current, latest, check_latest)

        return {
            "mods_dir": str(self.mods_path),
            "loader": loader,
            "game_versions": game_versions,
            "mods": mods,
            "summary": self._summary(mods),
        }

    def update_available(
        self, loader: str | None = None, game_version: str | None = None
    ):
        scan = self.scan(loader, game_version)
        updates = [mod for mod in scan["mods"] if mod["status"] == "update_available"]
        if not updates:
            scan["summary"]["updated"] = 0
            return scan

        backup_dir = self._backup_dir()
        updated = 0
        for mod in updates:
            result = self._apply_update(mod, backup_dir)
            mod.update(result)
            if result["status"] == "updated":
                updated += 1
        scan["summary"] = self._summary(scan["mods"])
        scan["summary"]["updated"] = updated
        scan["backup_dir"] = str(backup_dir)
        return scan

    def _clean_loader(self, loader: str | None) -> str:
        if not loader:
            return ""
        loader = loader.lower().strip()
        if loader not in self.SUPPORTED_LOADERS:
            return ""
        return loader

    @staticmethod
    def _clean_game_versions(game_version: str | None) -> list[str]:
        if not game_version:
            return []
        versions = []
        for part in str(game_version).split(","):
            clean = part.strip()
            if clean:
                versions.append(clean)
        return versions

    def _installed_jars(self) -> list[Path]:
        if not self.mods_path.exists():
            return []
        if not Helpers.is_subdir(str(self.mods_path), str(self.server_path)):
            raise ValueError("Mods directory is outside of the server path")
        return sorted(
            path
            for path in self.mods_path.iterdir()
            if path.is_file() and path.suffix.lower() == ".jar"
        )

    def _local_mod_entry(self, path: Path) -> dict[str, Any]:
        metadata = self._read_jar_metadata(path)
        return {
            "filename": path.name,
            "path": str(path),
            "name": metadata.get("name") or path.stem,
            "mod_id": metadata.get("mod_id", ""),
            "installed_version": metadata.get("version", ""),
            "metadata_loader": metadata.get("loader", ""),
            "source": "Local jar",
            "source_project_id": "",
            "source_version_id": "",
            "source_version_number": "",
            "latest_version": "",
            "latest_file": None,
            "sha1": self._hash_file(path, "sha1"),
            "sha512": self._hash_file(path, "sha512"),
            "size": path.stat().st_size,
            "status": "needs_review",
            "message": "No supported update source was found for this jar.",
        }

    def _get_modrinth_versions(self, hashes: list[str]) -> dict[str, Any]:
        return self._post_modrinth(
            "/version_files",
            {"hashes": hashes, "algorithm": "sha512"},
            default={},
        )

    def _get_modrinth_latest_versions(
        self, hashes: list[str], loader: str, game_versions: list[str]
    ) -> dict[str, Any]:
        versions = {}
        for file_hash in hashes:
            version = self._post_modrinth(
                f"/version_file/{file_hash}/update?algorithm=sha512",
                {
                    "loaders": [loader],
                    "game_versions": game_versions,
                },
                default=None,
            )
            if version:
                versions[file_hash] = version
        return versions

    def _post_modrinth(self, endpoint: str, payload: dict[str, Any], default):
        try:
            response = self.session.post(
                f"{self.MODRINTH_API}{endpoint}",
                json=payload,
                headers=self.MODRINTH_HEADERS,
                timeout=15,
            )
            if response.status_code == 404:
                return default
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.warning("Modrinth request failed for %s: %s", endpoint, exc)
            return default

    def _merge_modrinth_data(
        self,
        mod: dict[str, Any],
        current: dict[str, Any] | None,
        latest: dict[str, Any] | None,
        check_latest: bool,
    ) -> None:
        if not current:
            return

        current_file = self._version_primary_file(current)
        mod.update(
            {
                "name": current.get("name") or mod["name"],
                "source": "Modrinth",
                "source_project_id": current.get("project_id", ""),
                "source_version_id": current.get("id", ""),
                "source_version_number": current.get("version_number", ""),
                "installed_version": mod["installed_version"]
                or current.get("version_number", ""),
                "game_versions": current.get("game_versions", []),
                "loaders": current.get("loaders", []),
                "current_file": current_file,
                "status": "recognized",
                "message": "Recognized on Modrinth.",
            }
        )
        if not check_latest:
            return

        if not latest:
            mod.update(
                {
                    "status": "no_compatible_update",
                    "message": "No compatible update was found for this loader and Minecraft version.",
                }
            )
            return

        latest_file = self._version_primary_file(latest)
        latest_hash = latest_file.get("hashes", {}).get("sha512", "")
        mod.update(
            {
                "latest_version": latest.get("version_number", ""),
                "latest_version_id": latest.get("id", ""),
                "latest_game_versions": latest.get("game_versions", []),
                "latest_loaders": latest.get("loaders", []),
                "latest_file": latest_file,
                "required_dependencies": [
                    dep
                    for dep in latest.get("dependencies", [])
                    if dep.get("dependency_type") == "required"
                ],
            }
        )
        if latest_hash and latest_hash != mod["sha512"]:
            mod.update(
                {
                    "status": "update_available",
                    "message": "A compatible Modrinth update is available.",
                }
            )
        else:
            mod.update(
                {
                    "status": "up_to_date",
                    "message": "Installed file already matches the latest compatible Modrinth file.",
                }
            )

    @staticmethod
    def _version_primary_file(version: dict[str, Any] | None) -> dict[str, Any]:
        if not version:
            return {}
        files = version.get("files") or []
        if not files:
            return {}
        primary = next((file for file in files if file.get("primary")), None)
        return primary or files[0]

    def _apply_update(self, mod: dict[str, Any], backup_dir: Path) -> dict[str, Any]:
        latest_file = mod.get("latest_file") or {}
        download_url = latest_file.get("url", "")
        latest_hash = latest_file.get("hashes", {}).get("sha512", "")
        new_filename = os.path.basename(latest_file.get("filename") or mod["filename"])
        if not download_url.startswith("https://"):
            return {
                "status": "failed",
                "message": "Update file does not use HTTPS.",
            }
        if not new_filename.lower().endswith(".jar"):
            return {
                "status": "failed",
                "message": "Update file is not a jar.",
            }

        original = Path(mod["path"]).resolve()
        target = (self.mods_path / new_filename).resolve()
        if not Helpers.is_subdir(str(original), str(self.mods_path)):
            return {
                "status": "failed",
                "message": "Installed jar is outside the mods directory.",
            }
        if not Helpers.is_subdir(str(target), str(self.mods_path)):
            return {
                "status": "failed",
                "message": "Update filename resolved outside the mods directory.",
            }
        if target.exists() and target != original:
            return {
                "status": "failed",
                "message": f"Target file already exists: {target.name}",
            }

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / original.name
            shutil.copy2(original, backup_path)
            temp_path = self._download_update(download_url, latest_hash)
            os.replace(temp_path, target)
            if target != original and original.exists():
                original.unlink()
            return {
                "filename": target.name,
                "path": str(target),
                "status": "updated",
                "message": f"Updated from {mod['filename']} to {target.name}.",
                "backup_path": str(backup_path),
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to API/UI
            logger.warning("Failed to update mod %s: %s", mod["filename"], exc)
            return {
                "status": "failed",
                "message": f"Update failed: {exc}",
            }

    def _download_update(self, url: str, expected_sha512: str) -> Path:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".jar",
            dir=str(self.mods_path),
        ) as temp_file:
            temp_path = Path(temp_file.name)
            try:
                with self.session.get(
                    url, headers=self.MODRINTH_HEADERS, stream=True, timeout=60
                ) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            temp_file.write(chunk)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        actual_hash = self._hash_file(temp_path, "sha512")
        if expected_sha512 and actual_hash != expected_sha512:
            temp_path.unlink(missing_ok=True)
            raise ValueError("Downloaded jar hash did not match Modrinth metadata")
        return temp_path

    def _backup_dir(self) -> Path:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        return self.server_path / ".crafty" / "mod-update-backups" / stamp

    @staticmethod
    def _hash_file(path: Path, algorithm: str) -> str:
        digest = hashlib.new(algorithm)
        with open(path, "rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _read_jar_metadata(path: Path) -> dict[str, str]:
        metadata: dict[str, str] = {}
        try:
            with zipfile.ZipFile(path) as jar:
                if "fabric.mod.json" in jar.namelist():
                    data = json.loads(jar.read("fabric.mod.json").decode("utf-8"))
                    metadata.update(
                        {
                            "loader": "fabric",
                            "mod_id": data.get("id", ""),
                            "name": data.get("name", ""),
                            "version": data.get("version", ""),
                        }
                    )
                elif "quilt.mod.json" in jar.namelist():
                    data = json.loads(jar.read("quilt.mod.json").decode("utf-8"))
                    quilt_loader = data.get("quilt_loader", {})
                    quilt_meta = quilt_loader.get("metadata", {})
                    metadata.update(
                        {
                            "loader": "quilt",
                            "mod_id": quilt_loader.get("id", ""),
                            "name": quilt_meta.get("name", ""),
                            "version": quilt_loader.get("version", ""),
                        }
                    )
                else:
                    toml_path = next(
                        (
                            name
                            for name in (
                                "META-INF/mods.toml",
                                "META-INF/neoforge.mods.toml",
                            )
                            if name in jar.namelist()
                        ),
                        None,
                    )
                    if toml_path:
                        text = jar.read(toml_path).decode("utf-8", errors="ignore")
                        metadata.update(
                            {
                                "loader": (
                                    "neoforge" if "neoforge" in toml_path else "forge"
                                ),
                                "mod_id": ModUpdateManager._toml_value(text, "modId"),
                                "name": ModUpdateManager._toml_value(
                                    text, "displayName"
                                ),
                                "version": ModUpdateManager._toml_value(
                                    text, "version"
                                ),
                            }
                        )
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            logger.debug("Unable to read mod metadata from %s: %s", path, exc)
        return {key: value for key, value in metadata.items() if value}

    @staticmethod
    def _toml_value(text: str, key: str) -> str:
        match = re.search(
            rf'^\s*{re.escape(key)}\s*=\s*["\']([^"\']+)["\']', text, re.M
        )
        return match.group(1) if match else ""

    @staticmethod
    def _summary(mods: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "installed": len(mods),
            "recognized": sum(1 for mod in mods if mod["source"] == "Modrinth"),
            "updates": sum(1 for mod in mods if mod["status"] == "update_available"),
            "updated": sum(1 for mod in mods if mod["status"] == "updated"),
            "up_to_date": sum(1 for mod in mods if mod["status"] == "up_to_date"),
            "needs_review": sum(
                1
                for mod in mods
                if mod["status"] in {"needs_review", "no_compatible_update", "failed"}
            ),
        }
