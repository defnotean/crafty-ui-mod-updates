from pathlib import Path

from app.classes.shared.mod_updater import ModrinthModUpdater


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


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


def test_update_replaces_matched_modrinth_file(tmp_path: Path):
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    old_mod = mods_dir / "example-1.0.jar"
    old_mod.write_bytes(b"old mod contents")
    old_hash = ModrinthModUpdater.sha1_file(old_mod)
    new_bytes = b"new mod contents"

    def fake_post(url, json, headers, timeout):
        assert json["hashes"] == [old_hash]
        assert json["loaders"] == ["fabric"]
        assert json["game_versions"] == ["1.21.4"]
        return FakeResponse(
            {
                old_hash: {
                    "files": [
                        {
                            "primary": True,
                            "filename": "example-2.0.jar",
                            "url": "https://cdn.modrinth.com/example-2.0.jar",
                            "hashes": {
                                "sha1": ModrinthModUpdater.sha1_file_from_bytes(
                                    new_bytes
                                )
                            },
                        }
                    ]
                }
            }
        )

    def fake_downloader(url, out_path, out_file, headers):
        Path(out_path, out_file).write_bytes(new_bytes)
        return True

    updater = ModrinthModUpdater(http_post=fake_post, downloader=fake_downloader)
    result = updater.update(tmp_path, ["fabric"], ["1.21.4"])

    assert result.checked == 1
    assert result.updated == 1
    assert not old_mod.exists()
    assert (mods_dir / "example-2.0.jar").read_bytes() == new_bytes
