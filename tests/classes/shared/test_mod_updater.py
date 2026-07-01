import hashlib
import json
import warnings
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from app.classes.shared.mod_updater import ModrinthModUpdater


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


def create_mod_jar(path: Path, version="1.0.0") -> bytes:
    metadata = {
        "schemaVersion": 1,
        "id": "example",
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


def test_detects_loader_and_game_version_from_server_strings():
    assert ModrinthModUpdater.detect_loaders(
        "fabric-server-launch.jar", "java -jar fabric-server-launch.jar"
    ) == ["fabric"]
    assert ModrinthModUpdater.detect_loaders(
        "neoforge-21.1.1.jar", "java -jar neoforge-21.1.1.jar"
    ) == ["neoforge"]
    assert ModrinthModUpdater.detect_game_versions(
        "Paper 1.21.4 build 123", "server.jar"
    ) == ["1.21.4"]


def test_scan_to_result_handles_missing_summary_keys():
    from app.classes.shared.mod_updater import _scan_to_result

    result = _scan_to_result({"mods": [], "summary": {}})
    assert result.checked == 0
    assert result.updated == 0
    assert result.skipped == 0


def test_update_delegates_to_mod_update_manager(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    current_path = mods_dir / "example-1.0.0.jar"
    current_bytes = create_mod_jar(current_path)
    latest_path = tmp_path / "latest.jar"
    latest_bytes = create_mod_jar(latest_path, version="1.1.0")
    current_hash = sha512(current_bytes)
    latest_hash = sha512(latest_bytes)

    session = FakeSession(
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
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        updater = ModrinthModUpdater(session=session)
        result = updater.update(tmp_path, ["fabric"], ["1.21.1"])

    assert result.checked == 1
    assert result.updated == 1
    assert not current_path.exists()
    assert (mods_dir / "example-1.1.0.jar").read_bytes() == latest_bytes
