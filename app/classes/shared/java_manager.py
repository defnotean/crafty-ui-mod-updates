"""Automatic per-Minecraft-version Java provisioning.

Works out which Java major a server needs and downloads a matching Temurin JRE
on demand, so any Minecraft version starts with the correct Java with zero manual
setup (e.g. MC 26.1.2 -> Java 25, 1.20.1 -> Java 17, 1.8 -> Java 8).

Detection order (most authoritative first):
  1. the server jar's bundled ``version.json`` -> ``javaVersion.majorVersion``
     (vanilla / Paper carry this exact field Mojang itself uses)
  2. an MC version string (from the jar's ``version.json`` id /
     Fabric ``install.properties`` game-version / the download URL) looked up in
     Mojang's official version manifest -> ``javaVersion.majorVersion``
  3. a static heuristic by version number (offline fallback)

Everything is best-effort and side-effect free on failure: callers fall back to
the system Java, exactly as before this feature existed.
"""

import json
import logging
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import zipfile

import requests

from app.classes.helpers.helpers import Helpers

logger = logging.getLogger(__name__)


class JavaManager:
    MANIFEST_URL = (
        "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
    )
    ADOPTIUM = (
        "https://api.adoptium.net/v3/binary/latest/{major}/ga/"
        "{os}/{arch}/jre/hotspot/normal/eclipse"
    )
    HEADERS = {"User-Agent": "CraftyController/4 (java provisioner)"}

    def __init__(self, base_dir=None):
        self._base = base_dir
        self._manifest_index = None  # version id -> url
        self._ver_major = {}         # version id -> java major
        self._lock = threading.Lock()
        self._dl_locks = {}

    def configure(self, base_dir):
        """Set the directory that holds managed JREs (idempotent)."""
        self._base = base_dir

    @property
    def base(self):
        return self._base or os.path.join(os.path.abspath(os.curdir), "java")

    # ------------------------------------------------------------ os / arch
    @staticmethod
    def _os_name():
        if Helpers.is_os_windows():
            return "windows"
        if sys.platform == "darwin":
            return "mac"
        return "linux"

    @staticmethod
    def _arch():
        machine = (platform.machine() or "").lower()
        if machine in ("amd64", "x86_64", "x64"):
            return "x64"
        if machine in ("aarch64", "arm64"):
            return "aarch64"
        if machine in ("x86", "i386", "i686"):
            return "x86"
        return "x64"

    @staticmethod
    def _java_exe():
        return "java.exe" if Helpers.is_os_windows() else "java"

    # ------------------------------------------------------------ detection
    def required_major(self, jar_path=None, candidates=None):
        """Best-effort Java major for a server. Returns an int, or None."""
        candidates = [str(c) for c in (candidates or []) if c]

        # 1) authoritative: the jar's own javaVersion
        direct = self.major_from_jar(jar_path)
        if direct:
            return direct

        # 2) an MC version string -> Mojang manifest
        jar_version = self._mc_version_from_jar(jar_path)
        lookup = ([jar_version] if jar_version else []) + candidates
        try:
            version_id = self.find_version_id(lookup)
            if version_id:
                major = self.major_from_version_id(version_id)
                if major:
                    return major
        except Exception as exc:  # noqa: BLE001
            logger.debug("Auto-Java manifest lookup failed: %s", exc)

        # 3) heuristic
        return self.heuristic_major(lookup)

    @staticmethod
    def major_from_jar(jar_path):
        if not jar_path or not os.path.isfile(jar_path):
            return None
        try:
            with zipfile.ZipFile(jar_path) as archive:
                if "version.json" not in archive.namelist():
                    return None
                data = json.loads(
                    archive.read("version.json").decode("utf-8", "ignore")
                )
            major = (data.get("javaVersion") or {}).get("majorVersion")
            return int(major) if major else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _mc_version_from_jar(jar_path):
        if not jar_path or not os.path.isfile(jar_path):
            return None
        try:
            with zipfile.ZipFile(jar_path) as archive:
                names = archive.namelist()
                if "version.json" in names:
                    try:
                        data = json.loads(
                            archive.read("version.json").decode("utf-8", "ignore")
                        )
                        if data.get("id"):
                            return str(data["id"])
                    except Exception:  # noqa: BLE001
                        pass
                if "install.properties" in names:  # Fabric / Quilt launcher
                    try:
                        text = archive.read("install.properties").decode(
                            "utf-8", "ignore"
                        )
                        match = re.search(
                            r"(?mi)^\s*game-version\s*=\s*(.+?)\s*$", text
                        )
                        if match:
                            return match.group(1).strip()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            return None
        return None

    def _load_manifest(self):
        if self._manifest_index is not None:
            return self._manifest_index
        resp = requests.get(self.MANIFEST_URL, headers=self.HEADERS, timeout=20)
        resp.raise_for_status()
        index = {}
        for ver in resp.json().get("versions", []):
            if ver.get("id") and ver.get("url"):
                index[str(ver["id"])] = ver["url"]
        self._manifest_index = index
        return index

    def find_version_id(self, candidates):
        index = self._load_manifest()
        text = " ".join(str(c) for c in candidates if c)
        # plausible version tokens, longest first so 1.20.5 beats 1.20
        tokens = sorted(
            set(re.findall(r"\d+\.\d+(?:\.\d+)?", text)), key=len, reverse=True
        )
        for token in tokens:
            if token in index:
                return token
        return None

    def major_from_version_id(self, version_id):
        if version_id in self._ver_major:
            return self._ver_major[version_id]
        url = self._load_manifest().get(version_id)
        if not url:
            return None
        resp = requests.get(url, headers=self.HEADERS, timeout=20)
        resp.raise_for_status()
        major = (resp.json().get("javaVersion") or {}).get("majorVersion")
        major = int(major) if major else None
        if major:
            self._ver_major[version_id] = major
        return major

    @staticmethod
    def parse_java_error(text):
        """If a line of server output indicates the running Java is too old,
        return the Java major it actually needs, else None.

        The JVM itself is authoritative: ``UnsupportedClassVersionError`` reports
        the bytecode "class file version" (52=Java 8, 61=17, 65=21, 69=25, …),
        and Java major = class_version - 44. Also catches the friendly
        "requires Java N" messages newer Minecraft/Paper print.
        """
        if not text:
            return None
        text = str(text)
        match = re.search(r"class file version (\d+)", text)
        if match:
            class_version = int(match.group(1))
            if class_version >= 46:  # >= Java 2; sanity floor
                return max(8, class_version - 44)
        match = re.search(
            r"(?:requires|needs|please use|use)\s+Java\s+(\d+)", text, re.IGNORECASE
        )
        if match:
            return int(match.group(1))
        match = re.search(
            r"Java\s+(\d+)\s+or\s+(?:higher|above|newer|later)", text, re.IGNORECASE
        )
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def override_major(server_path):
        """Read a per-server pinned Java major written by the self-heal path."""
        try:
            path = os.path.join(server_path, ".crafty-java")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    value = int(handle.read().strip())
                    return value if value > 0 else None
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def write_override(server_path, major):
        try:
            with open(
                os.path.join(server_path, ".crafty-java"), "w", encoding="utf-8"
            ) as handle:
                handle.write(str(int(major)))
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def heuristic_major(candidates):
        text = " ".join(str(c) for c in candidates if c)
        match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", text)
        if not match:
            return None
        major = int(match.group(1))
        minor = int(match.group(2))
        patch = int(match.group(3) or 0)
        if major == 1:
            if minor <= 16:
                return 8
            if minor == 17:
                return 16
            if minor <= 19:
                return 17
            if minor == 20:
                return 17 if patch < 5 else 21
            return 21  # 1.21+
        # calendar-versioned / unknown future: modern default (manifest covers real cases)
        return 21

    # ---------------------------------------------------------- provisioning
    def java_binary_path(self, major):
        candidate = os.path.join(self.base, str(major), "bin", self._java_exe())
        return candidate if os.path.isfile(candidate) else None

    def ensure_java(self, major):
        """Return a path to a ``java`` binary of ``major``, downloading a Temurin
        JRE if we don't already have one. Returns None on failure."""
        if not major:
            return None
        existing = self.java_binary_path(major)
        if existing:
            return existing
        with self._lock:
            lock = self._dl_locks.setdefault(major, threading.Lock())
        with lock:
            existing = self.java_binary_path(major)
            if existing:
                return existing
            try:
                return self._download_and_install(major)
            except Exception as exc:  # noqa: BLE001
                logger.error("Auto-Java: could not provision Java %s: %s", major, exc)
                return None

    def _download_and_install(self, major):
        os.makedirs(self.base, exist_ok=True)
        dest = os.path.join(self.base, str(major))
        url = self.ADOPTIUM.format(
            major=major, os=self._os_name(), arch=self._arch()
        )
        logger.info(
            "Auto-Java: downloading Temurin JRE %s for %s/%s …",
            major,
            self._os_name(),
            self._arch(),
        )
        tmp = tempfile.mkdtemp(prefix=f"jre{major}-")
        try:
            with requests.get(
                url, headers=self.HEADERS, stream=True, timeout=600, allow_redirects=True
            ) as resp:
                resp.raise_for_status()
                name = resp.url.split("?")[0].split("/")[-1] or f"jre{major}.bin"
                archive_path = os.path.join(tmp, name)
                with open(archive_path, "wb") as handle:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)

            extract_dir = os.path.join(tmp, "x")
            os.makedirs(extract_dir, exist_ok=True)
            # Adoptium's redirect chain hides the extension in a query param, so
            # detect the archive type by magic bytes rather than the filename.
            with open(archive_path, "rb") as probe:
                magic = probe.read(4)
            if magic[:2] == b"PK":  # zip (Windows)
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(extract_dir)
            else:  # tar.gz / tar.xz (Linux / macOS) — tarfile auto-detects
                with tarfile.open(archive_path) as tf:
                    tf.extractall(extract_dir)

            home = self._find_java_home(extract_dir)
            if not home:
                raise RuntimeError("could not locate bin/java in the downloaded JRE")
            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
            shutil.move(home, dest)

            binary = self.java_binary_path(major)
            if not binary:
                raise RuntimeError("java binary missing after install")
            logger.info("Auto-Java: installed Java %s at %s", major, binary)
            return binary
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _find_java_home(self, root):
        exe = self._java_exe()
        for dirpath, _dirnames, filenames in os.walk(root):
            if os.path.basename(dirpath) == "bin" and exe in filenames:
                return os.path.dirname(dirpath)
        return None


# module-level singleton
java_manager = JavaManager()
