import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from app.classes.helpers.file_helpers import FileHelpers

logger = logging.getLogger(__name__)

MODRINTH_UPDATE_URL = "https://api.modrinth.com/v2/version_files/update"
MODRINTH_HEADERS = {
    "User-Agent": "Crafty Controller (https://craftycontrol.com)",
    "Content-Type": "application/json",
}


@dataclass
class ModUpdateCandidate:
    path: Path
    sha1: str


@dataclass
class ModUpdateResult:
    checked: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"Checked {self.checked} files. Updated {self.updated}, "
            f"skipped {self.skipped}, failed {self.failed}."
        )


class ModrinthModUpdater:
    """Updates installed Minecraft mod/plugin jars through Modrinth hash lookups."""

    def __init__(
        self,
        http_post: Callable[..., Any] | None = None,
        downloader: Callable[..., bool] | None = None,
    ) -> None:
        self.http_post = http_post or requests.post
        self.downloader = downloader or FileHelpers.ssl_get_file

    @staticmethod
    def detect_game_versions(*values: str | None) -> list[str]:
        version_pattern = re.compile(r"(?<!\d)(1\.\d+(?:\.\d+)?)(?!\d)")
        for value in values:
            if not value:
                continue
            match = version_pattern.search(str(value))
            if match:
                return [match.group(1)]
        return []

    @staticmethod
    def detect_loaders(*values: str | None) -> list[str]:
        haystack = " ".join(str(value) for value in values if value).lower()
        if not haystack:
            return []

        loader_markers = (
            ("neoforge", ("neoforge", "neo-forge")),
            ("fabric", ("fabric",)),
            ("quilt", ("quilt",)),
            ("forge", ("forge",)),
            ("paper", ("paper",)),
            ("purpur", ("purpur",)),
            ("spigot", ("spigot",)),
            ("bukkit", ("bukkit", "craftbukkit")),
            ("folia", ("folia",)),
            ("sponge", ("sponge",)),
        )
        loaders = []
        for loader, markers in loader_markers:
            if loader == "forge" and "neoforge" in loaders:
                continue
            if any(marker in haystack for marker in markers):
                loaders.append(loader)
        return loaders

    @staticmethod
    def sha1_file(path: Path) -> str:
        digest = hashlib.sha1()
        with path.open("rb") as jar_file:
            for chunk in iter(lambda: jar_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def sha1_file_from_bytes(contents: bytes) -> str:
        return hashlib.sha1(contents).hexdigest()

    def discover_candidates(self, server_path: str | Path) -> list[ModUpdateCandidate]:
        candidates: list[ModUpdateCandidate] = []
        for child_dir in ("mods", "plugins"):
            jar_dir = Path(server_path, child_dir)
            if not jar_dir.is_dir():
                continue
            for jar_path in sorted(jar_dir.glob("*.jar")):
                if jar_path.is_file():
                    candidates.append(
                        ModUpdateCandidate(jar_path, self.sha1_file(jar_path))
                    )
        return candidates

    def fetch_updates(
        self,
        hashes: list[str],
        loaders: list[str],
        game_versions: list[str],
    ) -> dict[str, Any]:
        response = self.http_post(
            MODRINTH_UPDATE_URL,
            json={
                "hashes": hashes,
                "algorithm": "sha1",
                "loaders": loaders,
                "game_versions": game_versions,
            },
            headers=MODRINTH_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Modrinth returned an unexpected update response.")
        return data

    @staticmethod
    def _select_download_file(version: dict[str, Any]) -> dict[str, Any] | None:
        files = version.get("files", [])
        if not isinstance(files, list):
            return None
        primary_files = [file for file in files if file.get("primary")]
        for file_info in primary_files + files:
            filename = str(file_info.get("filename", ""))
            if filename.lower().endswith(".jar") and file_info.get("url"):
                return file_info
        return None

    def update(
        self,
        server_path: str | Path,
        loaders: list[str],
        game_versions: list[str],
    ) -> ModUpdateResult:
        result = ModUpdateResult()
        candidates = self.discover_candidates(server_path)
        result.checked = len(candidates)

        if not candidates:
            result.messages.append("No mod or plugin .jar files were found.")
            return result

        updates = self.fetch_updates(
            [candidate.sha1 for candidate in candidates], loaders, game_versions
        )

        for candidate in candidates:
            version = updates.get(candidate.sha1)
            if not isinstance(version, dict):
                result.skipped += 1
                result.messages.append(f"No Modrinth match for {candidate.path.name}.")
                continue

            file_info = self._select_download_file(version)
            if file_info is None:
                result.skipped += 1
                result.messages.append(
                    f"No downloadable jar for {candidate.path.name}."
                )
                continue

            expected_sha1 = file_info.get("hashes", {}).get("sha1")
            if expected_sha1 == candidate.sha1:
                result.skipped += 1
                result.messages.append(f"{candidate.path.name} is already current.")
                continue

            filename = Path(str(file_info["filename"])).name
            if not filename.lower().endswith(".jar"):
                result.skipped += 1
                result.messages.append(
                    f"Skipped unsafe filename for {candidate.path.name}."
                )
                continue

            target_path = candidate.path.with_name(filename)
            if (
                target_path.exists()
                and target_path.resolve() != candidate.path.resolve()
            ):
                result.failed += 1
                result.messages.append(
                    f"Skipped {candidate.path.name}; target {filename} already exists."
                )
                continue

            temp_name = f".crafty-update-{filename}"
            temp_path = candidate.path.with_name(temp_name)
            if temp_path.exists():
                temp_path.unlink()

            downloaded = self.downloader(
                file_info["url"],
                str(candidate.path.parent),
                temp_name,
                headers=MODRINTH_HEADERS,
            )
            if not downloaded:
                result.failed += 1
                result.messages.append(f"Download failed for {filename}.")
                continue

            if expected_sha1 and self.sha1_file(temp_path) != expected_sha1:
                temp_path.unlink(missing_ok=True)
                result.failed += 1
                result.messages.append(f"Hash check failed for {filename}.")
                continue

            if candidate.path.resolve() != target_path.resolve():
                candidate.path.unlink()
            temp_path.replace(target_path)
            result.updated += 1
            result.messages.append(f"Updated {candidate.path.name} to {filename}.")

        return result
