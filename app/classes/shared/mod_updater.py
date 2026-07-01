"""Backward-compatible shim for mod updates.

Deprecated: use ``ModUpdateManager`` from ``mod_update_manager`` directly.
"""

from __future__ import annotations

import hashlib
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.classes.shared.mod_update_manager import ModUpdateManager

logger = logging.getLogger(__name__)


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


def _scan_to_result(scan: dict[str, Any]) -> ModUpdateResult:
    mods = scan.get("mods", [])
    if not isinstance(mods, list):
        mods = []
    summary = scan.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    result = ModUpdateResult()
    result.checked = int(summary.get("installed", len(mods)))
    result.updated = int(summary.get("updated", 0))
    result.failed = sum(1 for mod in mods if mod.get("status") == "failed")
    result.skipped = max(0, result.checked - result.updated - result.failed)
    for mod in mods:
        message = mod.get("message", "")
        if message:
            result.messages.append(message)
    return result


class ModrinthModUpdater:
    """Deprecated wrapper around :class:`ModUpdateManager`."""

    def __init__(self, session=None, **_legacy_kwargs) -> None:
        if _legacy_kwargs:
            warnings.warn(
                "ModrinthModUpdater injection kwargs are deprecated; "
                "pass a requests.Session to ModUpdateManager instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._session = session

    @staticmethod
    def detect_game_versions(*values: str | None) -> list[str]:
        server_stats = {"version": values[0]} if values else {}
        server_data = {
            "executable": values[1] if len(values) > 1 else "",
            "execution_command": values[2] if len(values) > 2 else "",
        }
        version = ModUpdateManager.infer_game_version(server_stats, server_data)
        return [version] if version else []

    @staticmethod
    def detect_loaders(*values: str | None) -> list[str]:
        haystack = " ".join(str(value) for value in values if value).lower()
        if not haystack:
            return []
        # neoforge before forge — "neoforge" contains "forge" as a substring
        for loader in ("neoforge", "fabric", "quilt", "forge"):
            if loader in haystack:
                return [loader]
        loader = ModUpdateManager.infer_loader(
            {
                "executable": values[0] if len(values) > 0 else "",
                "execution_command": values[1] if len(values) > 1 else "",
            }
        )
        return [loader] if loader else []

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
        warnings.warn(
            "ModrinthModUpdater.discover_candidates is deprecated; "
            "use ModUpdateManager.scan instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        manager = ModUpdateManager(server_path, session=self._session)
        return [
            ModUpdateCandidate(Path(mod["path"]), mod.get("sha1", ""))
            for mod in manager.scan().get("mods", [])
        ]

    def update(
        self,
        server_path: str | Path,
        loaders: list[str],
        game_versions: list[str],
    ) -> ModUpdateResult:
        warnings.warn(
            "ModrinthModUpdater is deprecated; use ModUpdateManager.update_available.",
            DeprecationWarning,
            stacklevel=2,
        )
        loader = loaders[0] if loaders else ""
        game_version = game_versions[0] if game_versions else ""
        manager = ModUpdateManager(server_path, session=self._session)
        scan = manager.update_available(loader, game_version)
        return _scan_to_result(scan)
