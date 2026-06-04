"""Shared helpers for building a Crafty server from a modpack.

Handles both Modrinth ``.mrpack`` and CurseForge ``.zip`` packs and keeps the
common create -> wait-for-jar -> download-files -> apply-overrides -> eula steps
in one place so the Modrinth, CurseForge, and file-upload entry points stay thin.
"""

import json
import logging
import os
import shutil
import time
import zipfile

from app.classes.helpers.helpers import Helpers

logger = logging.getLogger(__name__)

# Modrinth dependency key -> big_bucket jar type. Quilt has no catalog jar; fall
# back to Fabric (Quilt is Fabric-compatible) for v1.
MODRINTH_LOADER_MAP = {
    "fabric-loader": "fabric",
    "quilt-loader": "fabric",
    "forge": "forge-installer",
    "neoforge": "neoforge-installer",
}
# CurseForge modLoader id prefix -> big_bucket jar type.
CURSEFORGE_LOADER_MAP = {
    "forge": "forge-installer",
    "neoforge": "neoforge-installer",
    "fabric": "fabric",
    "quilt": "fabric",
}


# --------------------------------------------------------------------------- #
#  Generic build/install plumbing
# --------------------------------------------------------------------------- #
def build_create_payload(name, jar_type, mc_version, mem_min, mem_max, port):
    """Build the create_api_server payload for a Minecraft-Java loader server."""
    return {
        "name": name,
        "roles": [],
        "monitoring_type": "minecraft_java",
        "minecraft_java_monitoring_data": {"host": "127.0.0.1", "port": int(port)},
        "create_type": "minecraft_java",
        "minecraft_java_create_data": {
            "create_type": "download_jar",
            "download_jar_create_data": {
                "category": "mc_java_servers",
                "type": jar_type,
                "version": mc_version,
                "mem_min": mem_min,
                "mem_max": mem_max,
                "server_properties_port": int(port),
            },
        },
    }


def wait_for_import(controller, server_id, tries=180, delay=2):
    """Block until create_api_server's threaded jar download/import finishes."""
    for _ in range(tries):
        try:
            if not controller.servers.get_import_status(server_id):
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def server_root(controller, server_id):
    srv = controller.servers.get_server_data_by_id(server_id) or {}
    path = srv.get("path")
    if not path or not os.path.isdir(path):
        return None
    return os.path.abspath(path)


def safe_join(root, rel):
    """Join ``rel`` under ``root``, returning None on path traversal."""
    dest = os.path.abspath(os.path.join(root, rel))
    if not Helpers.is_subdir(dest, root):
        return None
    return dest


def extract_overrides(zip_path, root, prefixes):
    """Copy ``<prefix>/...`` entries from the pack zip into the server root."""
    for prefix in prefixes:
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for name in archive.namelist():
                    if name.endswith("/") or not name.startswith(prefix + "/"):
                        continue
                    rel = name[len(prefix) + 1:]
                    if not rel:
                        continue
                    dest = safe_join(root, rel)
                    if not dest:
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with archive.open(name) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
        except Exception as exc:  # noqa: BLE001
            logger.debug("overrides (%s) extraction issue: %s", prefix, exc)


def write_eula(root):
    try:
        with open(os.path.join(root, "eula.txt"), "w", encoding="utf-8") as eula:
            eula.write("eula=true")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Pack parsing
# --------------------------------------------------------------------------- #
def detect_pack_type(zip_path):
    """Return 'modrinth', 'curseforge', or None for a pack archive."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
    except Exception:
        return None
    if "modrinth.index.json" in names:
        return "modrinth"
    if "manifest.json" in names:
        return "curseforge"
    return None


def parse_mrpack(mrpack_path):
    """Return (mc_version, jar_type, index) for a Modrinth .mrpack."""
    with zipfile.ZipFile(mrpack_path) as archive:
        index = json.loads(archive.read("modrinth.index.json").decode("utf-8"))
    deps = index.get("dependencies", {}) or {}
    mc_version = deps.get("minecraft")
    loader_key = next((k for k in MODRINTH_LOADER_MAP if k in deps), None)
    if not mc_version or not loader_key:
        raise ValueError(
            "Modpack is missing a Minecraft version or a supported loader "
            "(fabric/quilt/forge/neoforge)."
        )
    return mc_version, MODRINTH_LOADER_MAP[loader_key], index


def parse_cf_manifest(zip_path):
    """Return (mc_version, jar_type, manifest) for a CurseForge modpack .zip."""
    with zipfile.ZipFile(zip_path) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    mc = manifest.get("minecraft", {}) or {}
    mc_version = mc.get("version")
    loaders = mc.get("modLoaders", []) or []
    primary = next(
        (loader for loader in loaders if loader.get("primary")),
        loaders[0] if loaders else None,
    )
    if not mc_version or not primary:
        raise ValueError("CurseForge manifest is missing a Minecraft version or mod loader.")
    loader_id = str(primary.get("id", ""))  # e.g. "forge-47.2.0", "neoforge-20.4.10"
    loader_name = loader_id.split("-")[0].lower()
    jar_type = CURSEFORGE_LOADER_MAP.get(loader_name)
    if not jar_type:
        raise ValueError(f"Unsupported CurseForge loader: {loader_id or 'unknown'}")
    return mc_version, jar_type, manifest


# --------------------------------------------------------------------------- #
#  Background installers (run in a daemon thread after the base jar imports)
# --------------------------------------------------------------------------- #
def install_modrinth(controller, server_id, temp_dir, mrpack_path, index):
    """Download a Modrinth pack's files + overrides into the new server."""
    from app.classes.shared.modrinth_manager import ModrinthManager

    try:
        mgr = ModrinthManager()
        wait_for_import(controller, server_id)
        root = server_root(controller, server_id)
        if not root:
            return
        for entry in index.get("files", []) or []:
            env = entry.get("env", {}) or {}
            if env.get("server") == "unsupported":
                continue
            rel = entry.get("path")
            downloads = entry.get("downloads", []) or []
            sha512 = (entry.get("hashes") or {}).get("sha512")
            if not rel or not downloads:
                continue
            dl_url = next(
                (u for u in downloads if str(u).lower().startswith("https://")), None
            )
            if not dl_url:
                logger.info("Skipping non-HTTPS modpack file: %s", rel)
                continue
            dest = safe_join(root, rel)
            if not dest:
                continue
            try:
                mgr.download_to(os.path.dirname(dest), dl_url, sha512, os.path.basename(rel))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Modpack file %s failed: %s", rel, exc)
        extract_overrides(mrpack_path, root, ["overrides", "server-overrides"])
        write_eula(root)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Modrinth install thread failed: %s", exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def install_curseforge(controller, server_id, temp_dir, zip_path, manifest):
    """Download a CurseForge pack's mods + overrides into the new server."""
    from app.classes.shared.curseforge_manager import CurseForgeManager

    try:
        mgr = CurseForgeManager()
        wait_for_import(controller, server_id)
        root = server_root(controller, server_id)
        if not root:
            return
        ok = 0
        failed = 0
        for entry in manifest.get("files", []) or []:
            pid = entry.get("projectID")
            fid = entry.get("fileID")
            if not pid or not fid:
                continue
            try:
                mgr.download_file(root, pid, fid, subdir="mods")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("CurseForge file %s/%s failed: %s", pid, fid, exc)
        logger.info(
            "CurseForge modpack %s install: %s mods ok, %s failed", server_id, ok, failed
        )
        extract_overrides(zip_path, root, [manifest.get("overrides", "overrides")])
        write_eula(root)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CurseForge install thread failed: %s", exc)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
