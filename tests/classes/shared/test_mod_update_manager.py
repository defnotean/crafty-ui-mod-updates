import hashlib
import json
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from app.classes.shared.mod_update_manager import ModUpdateManager


class FakeResponse:
    def __init__(self, status_code=200, payload=None, chunks=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.chunks = chunks or []

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1024):
        for chunk in self.chunks:
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, current_versions, latest_versions, download_bytes=b""):
        self.current_versions = current_versions
        self.latest_versions = latest_versions
        self.download_bytes = download_bytes

    def post(self, url, **_kwargs):
        path = urlparse(url).path
        if path.endswith("/version_files/update"):
            return FakeResponse(payload=self.latest_versions)
        if "/version_file/" in url and path.endswith("/update"):
            file_hash = path.split("/")[-2]
            payload = self.latest_versions.get(file_hash)
            if payload:
                return FakeResponse(payload=payload)
            return FakeResponse(status_code=404)
        if url.endswith("/version_files"):
            return FakeResponse(payload=self.current_versions)
        return FakeResponse(status_code=404)

    def get(self, _url, **_kwargs):
        return FakeResponse(chunks=[self.download_bytes])


def create_mod_jar(path: Path, mod_id="example", version="1.0.0") -> bytes:
    metadata = {
        "schemaVersion": 1,
        "id": mod_id,
        "name": "Example Mod",
        "version": version,
    }
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("fabric.mod.json", json.dumps(metadata))
    return path.read_bytes()


def sha512(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def version_payload(
    version_number, file_hash, filename, url="https://example.test/mod.jar"
):
    return {
        "id": f"version-{version_number}",
        "name": "Example Mod",
        "version_number": version_number,
        "project_id": "project-id",
        "game_versions": ["1.21.1"],
        "loaders": ["fabric"],
        "dependencies": [],
        "files": [
            {
                "primary": True,
                "filename": filename,
                "url": url,
                "hashes": {"sha512": file_hash},
                "size": 12,
            }
        ],
    }


def test_scan_detects_modrinth_update(tmp_path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    current_bytes = create_mod_jar(mods_dir / "example-1.0.0.jar")
    latest_hash = "a" * 128
    current_hash = sha512(current_bytes)

    manager = ModUpdateManager(
        tmp_path,
        session=FakeSession(
            current_versions={
                current_hash: version_payload(
                    "1.0.0", current_hash, "example-1.0.0.jar"
                )
            },
            latest_versions={
                current_hash: version_payload("1.1.0", latest_hash, "example-1.1.0.jar")
            },
        ),
    )

    scan = manager.scan("fabric", "1.21.1")

    assert scan["summary"]["installed"] == 1
    assert scan["summary"]["updates"] == 1
    assert scan["mods"][0]["status"] == "update_available"
    assert scan["mods"][0]["latest_version"] == "1.1.0"


def test_scan_without_game_version_keeps_recognized_status(tmp_path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    current_bytes = create_mod_jar(mods_dir / "example-1.0.0.jar")
    current_hash = sha512(current_bytes)

    manager = ModUpdateManager(
        tmp_path,
        session=FakeSession(
            current_versions={
                current_hash: version_payload(
                    "1.0.0", current_hash, "example-1.0.0.jar"
                )
            },
            latest_versions={},
        ),
    )

    scan = manager.scan("fabric", "")

    assert scan["summary"]["recognized"] == 1
    assert scan["summary"]["updates"] == 0
    assert scan["mods"][0]["status"] == "recognized"


def test_update_available_replaces_jar_and_creates_backup(tmp_path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    current_path = mods_dir / "example-1.0.0.jar"
    current_bytes = create_mod_jar(current_path)
    latest_path = tmp_path / "latest.jar"
    latest_bytes = create_mod_jar(latest_path, version="1.1.0")
    current_hash = sha512(current_bytes)
    latest_hash = sha512(latest_bytes)

    manager = ModUpdateManager(
        tmp_path,
        session=FakeSession(
            current_versions={
                current_hash: version_payload("1.0.0", current_hash, current_path.name)
            },
            latest_versions={
                current_hash: version_payload(
                    "1.1.0",
                    latest_hash,
                    "example-1.1.0.jar",
                    url="https://example.test/example-1.1.0.jar",
                )
            },
            download_bytes=latest_bytes,
        ),
    )

    result = manager.update_available("fabric", "1.21.1")

    assert result["summary"]["updated"] == 1
    assert not current_path.exists()
    assert (mods_dir / "example-1.1.0.jar").exists()
    assert Path(result["mods"][0]["backup_path"]).exists()
