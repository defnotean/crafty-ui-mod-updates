import datetime
import io
import logging
import mimetypes
import os
import pathlib
import re
import shutil
import ssl
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import certifi

from app.classes.models.server_permissions import PermissionsServers
from app.classes.helpers.cryptography_helper import CryptoHelper
from app.classes.helpers.helpers import Helpers
from app.classes.shared.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)

mimetypes.init(files=[])
SERVER_DETAIL = "/panel/server_detail"
PLAIN_TEXT = "text/plain"


class FileHelpers:
    allowed_quotes = ['"', "'", "`"]
    BYTE_TRUE: bytes = bytes.fromhex("01")
    BYTE_FALSE: bytes = bytes.fromhex("00")
    SNAPSHOT_BACKUP_DATE_FORMAT_STRING: str = "%Y-%m-%d-%H-%M-%S"

    def __init__(self, helper):
        self.helper: Helpers = helper
        self.add_mime_types()  # Add to account for yml, conf, properties, etc
        self.text_mime_prefixes = [
            "text/",
            "application/json",
            "application/xml",
            "application/javascript",
            "text/x-shellscript",
            "application/x-shellscript",
            "text/x-sh",
            "application/x-sh",
            "text/x-bat",
            "application/x-bat",
            "text/x-log",
        ]

    def add_mime_types(self):
        # Extend the default list
        mimetypes.add_type("text/yaml", ".yml")
        mimetypes.add_type("text/yaml", ".yaml")
        mimetypes.add_type("text/toml", ".toml")
        mimetypes.add_type(PLAIN_TEXT, ".ini")
        mimetypes.add_type(PLAIN_TEXT, ".conf")
        mimetypes.add_type(PLAIN_TEXT, ".properties")
        mimetypes.add_type(PLAIN_TEXT, ".prop")
        mimetypes.add_type(PLAIN_TEXT, ".env")
        mimetypes.add_type("application/x-bat", ".ps1")
        mimetypes.add_type("text/x-log", ".log")

    def can_unicode_decode(
        self, path: str, encoding: str = "utf-8", sample_size: int = 4096
    ) -> bool:
        """Check to see if file can be unicode decoded. Check for binary files

        Args:
            path (str): path to file to check
            encoding (str, optional): encoding profile. Defaults to "utf-8".
            sample_size (int, optional): size of sample to take. Defaults to 4096.

        Returns:
            bool: Returns true if file can be opened, false if not
        """
        try:
            with open(
                path,
                "rb",
            ) as sample:
                chunk = sample.read(sample_size)
            chunk.decode(encoding)
            if (
                b"\x00" in chunk
            ):  # check for empty bytes (binary files) this will also capture utf-16
                return False
            return True
        except (UnicodeDecodeError, PermissionError):
            return False

    def probably_can_open_file(self, path: str) -> tuple:
        if Path(path).is_dir():
            return (False, None)
        mime = mimetypes.guess_type(path)
        return (self.can_unicode_decode(path), mime[0])

    @staticmethod
    def ssl_get_file(  # pylint: disable=too-many-positional-arguments
        url, out_path, out_file, max_retries=3, backoff_factor=2, headers=None
    ):
        """
        Downloads a file from a given URL using HTTPS with SSL context verification,
        retries with exponential backoff and providing download progress feedback.

        Parameters:
            - url (str): The URL of the file to download. Must start with "https".
            - out_path (str): The local path where the file will be saved.
            - out_file (str): The name of the file to save the downloaded content as.
            - max_retries (int, optional): The maximum number of retry attempts
                in case of download failure. Defaults to 3.
            - backoff_factor (int, optional): The factor by which the wait time
                increases after each failed attempt. Defaults to 2.
            - headers (dict, optional):
                A dictionary of HTTP headers to send with the request.

        Returns:
            - bool: True if the download was successful, False otherwise.

        Raises:
            - urllib.error.URLError: If a URL error occurs during the download.
            - ssl.SSLError: If an SSL error occurs during the download.
        Exception: If an unexpected error occurs during the download.

        Note:
        This method logs critical errors and download progress information.
        Ensure that the logger is properly configured to capture this information.
        """
        if not url.lower().startswith("https"):
            logger.error("SSL File Get - Error: URL must start with https.")
            return False

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

        if not headers:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/58.0.3029.110 Safari/537.3"
                )
            }
        req = urllib.request.Request(url, headers=headers)

        write_path = os.path.join(out_path, out_file)
        attempt = 0

        logger.info(f"SSL File Get - Requesting remote: {url}")
        file_path_full = os.path.join(out_path, out_file)
        logger.info(f"SSL File Get - Download Destination: {file_path_full}")

        while attempt < max_retries:
            try:
                with urllib.request.urlopen(req, context=ssl_context) as response:
                    total_size = response.getheader("Content-Length")
                    if total_size:
                        total_size = int(total_size)
                    downloaded = 0
                    with open(write_path, "wb") as file:
                        while True:
                            chunk = response.read(1024 * 1024)  # 1 MB
                            if not chunk:
                                break
                            file.write(chunk)
                            downloaded += len(chunk)
                            if total_size:
                                progress = (downloaded / total_size) * 100
                                logger.info(
                                    f"SSL File Get - Download progress: {progress:.2f}%"
                                )
                                WebSocketManager().broadcast_page(
                                    SERVER_DETAIL,
                                    "download_progress",
                                    round(progress, 1),
                                )
                    return True
            except (urllib.error.URLError, ssl.SSLError) as e:
                logger.warning(f"SSL File Get - Attempt {attempt+1} failed: {e}")
                time.sleep(backoff_factor**attempt)
            except Exception as e:
                logger.critical(f"SSL File Get - Unexpected error: {e}")
                return False
            finally:
                attempt += 1

        logger.error("SSL File Get - Maximum retries reached. Download failed.")
        return False

    @staticmethod
    def del_dirs(path):
        path = pathlib.Path(path)
        clean = True
        for sub in path.iterdir():
            if sub.is_dir():
                # Delete folder if it is a folder
                FileHelpers.del_dirs(sub)
            else:
                # Delete file if it is a file:
                try:
                    sub.unlink()
                except Exception as e:
                    clean = False
                    logger.error(f"Unable to delete file {sub}: {e}")
        try:
            # This removes the top-level folder:
            path.rmdir()
        except Exception:
            logger.error("Unable to remove top level")
            return False
        return clean

    @staticmethod
    def del_file(path):
        path = pathlib.Path(path)
        clean = True
        try:
            logger.debug(f"Deleting file: {path}")
            # Remove the file
            os.remove(path)
            return clean
        except (FileNotFoundError, PermissionError):
            logger.error(f"Path specified is not a file or does not exist. {path}")
            clean = False
            return clean

    def check_mime_types(self, file_path):
        m_type, _value = mimetypes.guess_type(file_path)
        return m_type

    @staticmethod
    def copy_dir(src_path, dest_path, dirs_exist_ok=False):
        # pylint: disable=unexpected-keyword-arg
        shutil.copytree(src_path, dest_path, dirs_exist_ok=dirs_exist_ok)

    @staticmethod
    def copy_file(src_path, dest_path):
        shutil.copy(src_path, dest_path)

    @staticmethod
    def move_dir(src_path, dest_path):
        shutil.move(src_path, dest_path)

    @staticmethod
    def move_dir_exist(src_path, dest_path):
        FileHelpers.copy_dir(src_path, dest_path, True)
        FileHelpers.del_dirs(src_path)

    @staticmethod
    def move_file(src_path, dest_path):
        shutil.move(src_path, dest_path)

    @staticmethod
    def make_archive(path_to_destination, path_to_zip, comment=""):
        # create a ZipFile object
        string_target_path = str(path_to_destination)
        path_to_destination = string_target_path

        string_zip_path = str(path_to_zip)

        if not path_to_destination.endswith(".zip"):
            path_to_destination += ".zip"
        with ZipFile(path_to_destination, "w") as zip_file:
            zip_file.comment = bytes(
                comment, "utf-8"
            )  # comments over 65535 bytes will be truncated
            for root, _dirs, files in os.walk(string_zip_path, topdown=True):
                ziproot = string_zip_path
                for file in files:
                    try:
                        logger.info(f"backing up: {os.path.join(root, file)}")
                        if os.name == "nt":
                            zip_file.write(
                                os.path.join(root, file),
                                os.path.join(root.replace(ziproot, ""), file),
                            )
                        else:
                            zip_file.write(
                                os.path.join(root, file),
                                os.path.join(root.replace(ziproot, "/"), file),
                            )

                    except Exception as e:
                        logger.warning(
                            f"Error backing up: {os.path.join(root, file)}!"
                            f" - Error was: {e}"
                        )
        return True

    @staticmethod
    def make_compressed_archive(path_to_destination, path_to_zip, comment=""):
        # create a ZipFile object
        path_to_destination += ".zip"
        with ZipFile(path_to_destination, "w", ZIP_DEFLATED) as zip_file:
            zip_file.comment = bytes(
                comment, "utf-8"
            )  # comments over 65535 bytes will be truncated
            for root, _dirs, files in os.walk(path_to_zip, topdown=True):
                ziproot = path_to_zip
                for file in files:
                    try:
                        logger.info(f"packaging: {os.path.join(root, file)}")
                        if os.name == "nt":
                            zip_file.write(
                                os.path.join(root, file),
                                os.path.join(root.replace(ziproot, ""), file),
                            )
                        else:
                            zip_file.write(
                                os.path.join(root, file),
                                os.path.join(root.replace(ziproot, "/"), file),
                            )

                    except Exception as e:
                        logger.warning(
                            f"Error packaging: {os.path.join(root, file)}!"
                            f" - Error was: {e}"
                        )

        return True

    def make_backup(  # pylint: disable=too-many-positional-arguments
        self,
        path_to_destination,
        path_to_zip,
        excluded_dirs,
        server_id,
        backup_id,
        comment="",
        compressed=None,
    ):
        # create a ZipFile object
        path_to_destination += ".zip"
        ex_replace = [
            Path(self.get_absolute_path(path_to_zip, p)).as_posix()
            for p in excluded_dirs
        ]
        total_bytes = 0
        dir_bytes = FileHelpers.get_dir_size(path_to_zip)
        results = {
            "percent": 0,
            "total_files": self.helper.human_readable_file_size(dir_bytes),
        }
        WebSocketManager().broadcast_page_params(
            SERVER_DETAIL,
            {"id": str(server_id)},
            "backup_status",
            results,
        )
        WebSocketManager().broadcast_page_params(
            "/panel/edit_backup",
            {"id": str(server_id)},
            "backup_status",
            results,
        )
        # Set the compression mode based on the `compressed` parameter
        compression_mode = ZIP_DEFLATED if compressed else ZIP_STORED
        with ZipFile(path_to_destination, "w", compression_mode) as zip_file:
            zip_file.comment = bytes(
                comment, "utf-8"
            )  # comments over 65535 bytes will be truncated
            for root, dirs, files in os.walk(path_to_zip, topdown=True):
                for l_dir in dirs[:]:
                    # make all paths in exclusions a unix style slash
                    # to match directories.
                    if str(os.path.join(root, l_dir)).replace("\\", "/") in ex_replace:
                        dirs.remove(l_dir)
                ziproot = path_to_zip
                # iterate through list of files
                for file in files:
                    # check if file/dir is in exclusions list.
                    # Only proceed if not exluded.
                    if (
                        str(os.path.join(root, file)).replace("\\", "/")
                        not in ex_replace
                        and file != "crafty.sqlite"
                    ):
                        try:
                            logger.debug(f"backing up: {os.path.join(root, file)}")
                            # add trailing slash to zip root dir if not windows.
                            if os.name == "nt":
                                zip_file.write(
                                    os.path.join(root, file),
                                    os.path.join(root.replace(ziproot, ""), file),
                                )
                            else:
                                zip_file.write(
                                    os.path.join(root, file),
                                    os.path.join(root.replace(ziproot, "/"), file),
                                )

                        except Exception as e:
                            logger.warning(
                                f"Error backing up: {os.path.join(root, file)}!"
                                f" - Error was: {e}"
                            )
                    # debug logging for exlusions list
                    else:
                        logger.debug(f"Found {file} in exclusion list. Skipping...")

                    try:
                        # add current file bytes to total bytes.
                        total_bytes += os.path.getsize(os.path.join(root, file))
                    except FileNotFoundError as why:
                        logger.debug(f"Failed to calculate file size with error {why}")
                    # calcualte percentage based off total size and current archive size
                    percent = round((total_bytes / dir_bytes) * 100, 2)
                    # package results
                    results = {
                        "percent": percent,
                        "total_files": self.helper.human_readable_file_size(dir_bytes),
                        "backup_id": backup_id,
                    }
                    # send status results to page.
                    WebSocketManager().broadcast_page_params(
                        SERVER_DETAIL,
                        {"id": str(server_id)},
                        "backup_status",
                        results,
                    )
                    WebSocketManager().broadcast_page_params(
                        "/panel/edit_backup",
                        {"id": str(server_id)},
                        "backup_status",
                        results,
                    )
        return True

    def move_item_file_or_dir(self, old_dir, new_dir, item) -> None:
        """
        Move item to new location if it is either a file or a dir. Will raise
        shutil.Error for any errors encountered.

        Args:
            old_dir: Old location.
            new_dir: New location.
            item: File or directory name.

        Returns: None

        """
        try:
            # Check if source item is a directory or a file.
            if os.path.isdir(os.path.join(old_dir, item)):
                # Source item is a directory
                FileHelpers.move_dir_exist(
                    os.path.join(old_dir, item),
                    os.path.join(new_dir, item),
                )
            else:
                # Source item is a file.
                FileHelpers.move_file(
                    os.path.join(old_dir, item),
                    os.path.join(new_dir, item),
                )

        # Error raised by shutil if an error is encountered. Raising the same error if
        # encountered.
        except shutil.Error as why:
            raise RuntimeError(
                f"Error moving {old_dir} to {new_dir} with information: {why}"
            ) from why

    @staticmethod
    def restore_archive(archive_location, destination):
        with zipfile.ZipFile(archive_location, "r") as zip_ref:
            zip_ref.extractall(destination)

    def send_percentage(self, user, percent, proc_id, complete):
        if isinstance(user, str):
            WebSocketManager().broadcast_user(
                user,
                "zip_status",
                {"id": None, "percent": percent, "complete": complete},
            )
        else:
            for usr in user:
                WebSocketManager().broadcast_user(
                    usr,
                    "zip_status",
                    {"id": proc_id, "percent": percent, "complete": complete},
                )

    def should_extract(
        self, file, base_include_path, excluded_files, server_update
    ) -> bool:
        """Checks a number of inclusions or exclusions against a given file to see
        if that file should be unpacked to the target directory.

        ** Base include path and excluded files should not be used in conjunction with
        eachother.

        Args:
            file (str): file name from Path zip object namelist
            base_include_path (str): string from root dir select that shows base path
            like 'server_files/myserver/' (should not be used with excluded_files)
            excluded_files (list): list of file exclusions (should not be used with base
            include path)
            server_update (bool): whether or not the method was called as a result
            of a server update process.

        Returns:
            bool: Whether or not the file from the list should be included in the
            unzipped archive.
        """
        if server_update and file in excluded_files:
            return False

        if not base_include_path:
            return True

        try:
            pathlib.PurePosixPath(file).relative_to(
                pathlib.PurePosixPath(base_include_path)
            )
            return True
        except ValueError:
            return False

    def get_archive_internal_name(self, file, base_include_path) -> str:
        """If we have a base include path we will rewrite the internal zip object path
        to remove the /path/to/file/in/archive so we don't have nested folders when we
        unzip. This will return the relative path to the archive to avoid nesting.

        Args:
            file (str): file from namelist from zip object
            base_include_path (str): string from root dir select that shows base path
            like 'server_files/myserver/'

        Returns:
            str | PurePosixPath: returns the original file name or new file name
        """
        if base_include_path:  # Rewrite path of zip_ref_info if we have a base include
            try:
                rel = pathlib.PurePosixPath(file).relative_to(base_include_path)
                return str(rel)
            except ValueError:
                logger.debug("%s is not relative to %s", file, base_include_path)
        return str(file)

    def unzip_file(
        self,
        zip_path,
        destination_path,
        server_id=None,
        server_update: bool = False,
        proc_id=None,
        user_id=None,
        base_include_path=None,
    ) -> None:
        """
        Unzips zip file at zip_path to location generated at new_dir based on zip
        contents.

        Args:
            zip_path: Path to zip file to unzip.
            server_update: Will skip ignored items list if not set to true. Used for
            updating bedrock servers.

        Returns: None

        """
        ignored_names = [
            "server.properties",
            "permissions.json",
            "allowlist.json",
        ]
        server_users = user_id
        if not server_users:
            server_users = PermissionsServers.get_server_user_list(server_id)

        # make sure we're able to access the zip file
        if Helpers.check_file_perms(zip_path) and os.path.isfile(zip_path):
            # make sure the directory we're unzipping this to exists
            Helpers.ensure_dir_exists(destination_path)
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                files_list = zip_ref.namelist()
                for idx, file in enumerate(files_list):
                    info = zip_ref.getinfo(file)
                    # Skip directory entries
                    if info.is_dir():
                        continue
                    archive_name = self.get_archive_internal_name(
                        file, base_include_path
                    )
                    safe_archive_name = self.get_safe_archive_name(archive_name)
                    if not safe_archive_name:
                        continue
                    target = Path(destination_path, safe_archive_name).resolve()
                    try:
                        Helpers.validate_traversal(destination_path, target)
                    except ValueError:
                        self.send_percentage(server_users, 100, proc_id, True)
                        return logger.error("Traversal detected. Dumping out.")
                    # if the file is one of our ignored names we'll skip it
                    if self.should_extract(
                        file, base_include_path, ignored_names, server_update
                    ):
                        info.filename = safe_archive_name
                        try:
                            zip_ref.extract(info, destination_path)
                        except FileNotFoundError:
                            logger.error(
                                "Could not extract file: %s to %s from archive %s",
                                file,
                                destination_path,
                                zip_path,
                            )
                    percent = round((idx / len(files_list)) * 100)
                    self.send_percentage(server_users, percent, proc_id, False)
            self.send_percentage(server_users, 100, proc_id, True)

    @staticmethod
    def get_safe_archive_name(file: str) -> str:
        """Remove absolute and parent path segments from a zip member name."""
        parts = []
        for part in pathlib.PurePosixPath(file.replace("\\", "/")).parts:
            if part in ("", ".", "..", "/"):
                continue
            if re.match(r"^[A-Za-z]:$", part):
                continue
            parts.append(part)
        return str(pathlib.PurePosixPath(*parts)) if parts else ""

    @staticmethod
    def get_absolute_path(server_path, path) -> str:
        """Takes requested path and returns absolute path

        Args:
            server_path (str): requested server's root path
            path (str | path): requested file path

        Returns:
            _type_: Path
        """
        request_path = path
        if not Path(path).is_absolute():
            path = str(Path(server_path, request_path))

        return str(path)

    @staticmethod
    def get_chunk_path_from_hash(chunk_hash: bytes, repository_location: Path) -> Path:
        """
        Given chunk hash and repository location, gets full path to chunk in repo.

        Args:
            chunk_hash: Hash of chunk in bytes.
            repository_location: Path to the backup repository.

        Return: Path to chunk in repository.
        """
        hash_hex = CryptoHelper.bytes_to_hex(chunk_hash)
        if len(hash_hex) != 128:
            raise ValueError(
                f"Provided hash is of incorrect length."
                f"Hash: {CryptoHelper.bytes_to_hex(chunk_hash)}"
            )
        return repository_location / "chunks" / hash_hex[:2] / hash_hex[-126:]

    @staticmethod
    def get_file_path_from_hash(file_hash: bytes, repository_location: Path) -> Path:
        """
        Get path to file manifest file in repository location given file hash and
        repository location.

        Args:
            file_hash: Hash of file.
            repository_location: Path to the backup repository.

        Returns: Path to file manifest file in the backup repository.

        """
        hash_hex: str = CryptoHelper.bytes_to_hex(file_hash)
        if len(hash_hex) != 128:
            raise ValueError(
                f"Provided hash is of incorrect length."
                f"Hash: {CryptoHelper.bytes_to_hex(file_hash)}"
            )
        return repository_location / "files" / hash_hex[:2] / hash_hex[-126:]

    @staticmethod
    def discover_files(target_path: Path, exclusions) -> list[Path]:
        """
        Returns a list of all files in a target path, ignores empty directories.

        Args:
            target_path: Path to find all files in.

        Returns: List of all files in target path.

        """
        # Check that target is a directory.
        if not target_path.is_dir():
            raise NotADirectoryError(f"{target_path} is not a directory.")

        discovered_files = []
        excluded_dirs = []
        excluded_files = []

        for excl_dir in exclusions:
            temp_path = Path(excl_dir).resolve()
            if temp_path.is_file():
                excluded_files.append(temp_path)
            else:
                excluded_dirs.append(temp_path)

        # Use pathlib built in rglob to find all files.
        for p in target_path.rglob("*"):
            if p.is_dir():
                continue
            if p not in excluded_files and p.parents not in excluded_dirs:
                discovered_files.append(p)
        return discovered_files

    def clean_old_backups(self, num_to_keep: int, backup_repository_path: Path) -> None:
        """
        Remove all old backups from the backup repository based on number of backups to
        keep.

        Args:
            num_to_keep: Number of backups to keep. Keeps latest n.
            backup_repository_path: Path to the backup repository.

        Return:
        """
        if num_to_keep <= 0:
            return

        # get list of manifest files in the backup repo.
        manifest_files_path: Path = backup_repository_path / "manifests"
        manifest_files_generator = manifest_files_path.rglob("*")
        # List used later to delete manifest files.
        manifest_files_list: list[Path] = []

        # Extract the datetimes from the filenames of the manifest files.
        manifests_datetime: list[datetime.datetime] = []
        for manifest_file in manifest_files_generator:
            manifest_files_list.append(manifest_file)
            manifests_datetime.append(
                datetime.datetime.strptime(
                    manifest_file.name.split(".")[0],
                    self.SNAPSHOT_BACKUP_DATE_FORMAT_STRING,
                )
            )

        # sort list of manifests.
        # Oldest datetime events will be sorted first.
        manifests_datetime.sort()

        # Determine number of manifest files to remove
        # For example, we have 10, want to keep 7.
        # 10 - 7 = 3.
        num_to_remove = len(manifests_datetime) - num_to_keep

        # Return if we don't need to remove any files.
        if num_to_remove <= 0:
            return

        # Oldest first, delete n oldest files from list.
        for _ in range(num_to_remove):
            del manifests_datetime[0]

        # Delete manifest files that are no longer used.
        self.delete_unused_manifest_files(manifests_datetime, manifest_files_list)

        files_to_keep, chunks_to_keep = self.create_file_keepers_set(
            backup_repository_path, manifests_datetime
        )

        # Delete unused files and chunks.
        self.delete_unused_items_from_repository(
            files_to_keep, backup_repository_path, False
        )
        self.delete_unused_items_from_repository(
            chunks_to_keep, backup_repository_path, True
        )

    @staticmethod
    def delete_unused_items_from_repository(
        items_to_keep: set[bytes], backup_repository_path: Path, mode: bool
    ) -> None:
        """
        Delete unused chunks for files from the backup repository. Switches type based
        on mode.

        Args:
            items_to_keep: Set of chunks or files to keep.
            backup_repository_path: Path to backup repository.
            mode: False for file, True for chunks.

        Return:
        """
        # Mode False = files. True = chunks.
        if mode:
            item_manifests_path = backup_repository_path / "chunks"
        else:
            item_manifests_path = backup_repository_path / "files"
        item_generator = item_manifests_path.rglob("*")
        for item in item_generator:
            # Generator returns both directories and files. We can ignore directories.
            if item.is_dir():
                continue

            # Reconstruct item hash from item path.
            # Stored as first two octets as a directory and rest of hash as filename.
            item_hash: bytes = bytes.fromhex(str(item.parent.name) + str(item.name))

            # If item is not present in the ones that we want to keep, delete it.
            if item_hash not in items_to_keep:
                item.unlink()

    def delete_unused_manifest_files(
        self,
        manifest_files_to_keep: list[datetime.datetime],
        manifest_files_list: list[Path],
    ) -> None:
        """
        Deletes unused backup manifest files from the backup repository.
        :param manifest_files_to_keep: List of manifest files to keep. Datetime list of
        backups to keep.
        :param manifest_files_list: List of all files currently found in the backup
        repository.
        :return:
        """
        # This is a little nasty.
        # Iterate over files found in the backup repository.
        for manifest_file in manifest_files_list:
            # If that file, converted to a datetime, is not present in the files_to_keep
            # list.
            if (
                datetime.datetime.strptime(
                    manifest_file.name.split(".")[0],
                    self.SNAPSHOT_BACKUP_DATE_FORMAT_STRING,
                )
                not in manifest_files_to_keep
            ):
                # Delete the file.
                manifest_file.unlink(missing_ok=True)

    def create_file_keepers_set(
        self, backup_repository_path: Path, keepers_datetime_list
    ) -> (set[bytes], set[bytes]):
        """
        Creates a set of files to keep from a given backup manifest files to keep.

        Args:
            backup_repository_path: Path to backup repository.
            keepers_datetime_list: List of manifest files to keep. Datetime list.

        Returns: Set of files to keep, set of chunks to keep.
        """
        files_to_keep = set()
        for keeper_manifest_datetime in keepers_datetime_list:
            backup_time = keeper_manifest_datetime.strftime(
                self.SNAPSHOT_BACKUP_DATE_FORMAT_STRING
            )
            # Open file
            manifest_file_path = (
                backup_repository_path / "manifests" / f"{backup_time}.manifest"
            )
            try:
                manifest_file: io.TextIOWrapper = manifest_file_path.open("r")
            except OSError as why:
                raise RuntimeError(
                    f"Unable to open manifest file at {manifest_file_path}"
                ) from why

            # Check that manifest is readable with this version.
            if manifest_file.readline() != "00\n":
                manifest_file.close()
                raise RuntimeError(
                    f"Backup manifest is not of correct version. Manifest: "
                    f"{manifest_file_path}."
                )

            for line in manifest_file:
                # Add hash to keep to output set.
                files_to_keep.add(CryptoHelper.b64_to_bytes(line.split(":")[0]))

            # Close this file.
            manifest_file.close()

        keeper_chunks = set()

        # Iterate over files to keep, and record all chunks to keep for those files.
        for file_to_keep in files_to_keep:
            file_chunks = self.get_keeper_chunks_file_file_hash(
                backup_repository_path, file_to_keep
            )
            for chunk in file_chunks:
                keeper_chunks.add(chunk)
        return files_to_keep, keeper_chunks

    def get_keeper_chunks_file_file_hash(
        self, backup_repository_location: Path, file_hash: bytes
    ) -> list[bytes]:
        """
        Get chunks that should be kept based on given file.

        Args:
            backup_repository_location: Path to the backup repository.
            file_hash: Hash of file.

        Returns: List of chunk hashes that should be kept.

        """
        file_manifest_path: Path = self.get_file_path_from_hash(
            file_hash, backup_repository_location
        )

        # Open file and read keeper chunks.
        try:
            file_manifest_file = file_manifest_path.open("r")
        except OSError as why:
            raise RuntimeError(
                f"Unable to open file manifest file at {file_manifest_path}"
            ) from why

        if file_manifest_file.readline() != "00\n":
            file_manifest_file.close()
            raise RuntimeError(
                f"File manifest file {file_manifest_path} is not of a readable version."
            )

        output = set()

        for line in file_manifest_file:
            output.add(CryptoHelper.b64_to_bytes(line))

        output_list: list[bytes] = []
        for item in output:
            output_list.append(item)

        return output_list

    @staticmethod
    def get_local_path_with_base(desired_path: Path, base: Path) -> str:
        """
        Removes base from given path.
        Given:
            Path: /root/example.md
            Base: /root/
            Returns: example.md

        Args:
            desired_path: Path to file in base.
            base: Base file to remove from path.

        Returns: Local path to file.

        """
        # Check that file is contained in base, and the base is a directory.
        if base not in desired_path.parents:
            raise OSError(f"{desired_path} is not a child of {base}.")

        return str(desired_path.resolve())[len(str(base.resolve())) + 1 :]

    def save_file(
        self,
        source_file: Path,
        repository_location: Path,
        file_hash: bytes,
        use_compression: bool,
    ) -> None:
        """
        Saves given file to repository location. Will not save duplicate files or
        duplicate chunks. All errors resolve to RuntimeErrors.

        Args:
            source_file: Source file to save to the backup repository.
            repository_location: Path to the backup repository.
            file_hash: Hash of file.
            use_compression: If the file in the backup repository should be compressed.

        Returns:

        """
        # File is read and saved in 20mb chunks. Should allow memory use to stay low and
        # for files to be processed that are larger than available memory.
        try:
            file_manifest_file_location: Path = self.get_file_path_from_hash(
                file_hash, repository_location
            )
        except ValueError as why:
            raise RuntimeError(
                "Provided file hash does not appear to be of improper length!"
            ) from why

        # Exit if file is already present in the backup repository. Ensure that we don't
        # try to save the save file twice.
        if file_manifest_file_location.exists():
            return
        file_manifest_file_location.parent.mkdir(parents=True, exist_ok=True)

        # Open source file and start saving chunks.
        try:
            source_file_obj = source_file.open("rb")
        except OSError as why:
            raise RuntimeError(f"Unable to read file at {source_file}.") from why

        # Open target file manifest file to write chunks.
        try:
            file_manifest_file = file_manifest_file_location.open("w+")
        except OSError as why:
            source_file_obj.close()
            raise RuntimeError(
                f"Unable to open file manifest file at {file_manifest_file_location}."
            ) from why

        # Begin reading source and writing to manifest file.
        # Write file manifest file version number as first line.
        file_manifest_file.write("00\n")

        # Loop through file contents writing to both files until empty.
        while True:
            chunk = source_file_obj.read(10_000_000)

            if not chunk:
                # Completed reading source file, close out.
                source_file_obj.close()
                file_manifest_file.close()
                return

            # Write chunk to file manifest file.
            chunk_hash = CryptoHelper.blake2b_hash_bytes(chunk)
            chunk_hash_as_b64 = CryptoHelper.bytes_to_b64(chunk_hash)
            file_manifest_file.write(chunk_hash_as_b64 + "\n")

            try:
                self.save_chunk(chunk, repository_location, chunk_hash, use_compression)
            except RuntimeError as why:
                raise RuntimeError(
                    f"Unable to save chunk with hash {chunk_hash}."
                ) from why

    def save_chunk(
        self,
        file_chunk: bytes,
        repository_location: Path,
        chunk_hash: bytes,
        use_compression: bool,
    ) -> None:
        """
        Saves chunk to backup repository. Space is made in this version of the chunk
        for encryption, but that functionality is not yet present.

        Args:
            file_chunk: chunk data to save to file.
            repository_location: Path to repository.
            chunk_hash: hash of chunk.
            use_compression: If the chunk should be compressed before saving to file.

        Return:

        """
        file_location = self.get_chunk_path_from_hash(chunk_hash, repository_location)

        # If chunk is already present, stop here. Don't save the chunk again.
        if file_location.exists():
            return

        # Create folder for chunk.
        file_location.parent.mkdir(parents=True, exist_ok=True)

        # Chunk version number.
        version = bytes.fromhex("00")

        # Check and apply compression, write compression byte.
        if use_compression:
            file_chunk = self.zlib_compress_bytes(file_chunk)
            compression = self.BYTE_TRUE
        else:
            compression = self.BYTE_FALSE

        # Placeholder to allow for encryption in future versions
        encryption = self.BYTE_FALSE
        nonce = bytes.fromhex("000000000000000000000000")

        # Create chunk
        output = version + encryption + nonce + compression + file_chunk

        # Save chunk to file
        try:
            with file_location.open("wb+") as file:
                file.write(output)
        except OSError as why:
            raise RuntimeError(f"Unable to save chunk to {file_location}") from why

    def read_file(
        self, file_hash: bytes, target_path: Path, backup_repo_path: Path
    ) -> None:
        """
        Read file from file manifest, restores to target path.

        Args:
            file_hash: Hash of file to restore.
            target_path: Path to restore file to.
            backup_repo_path: Path to the backup repo.

        Returns:

        """
        # Get file manifest file path.
        try:
            source_file_manifest_path: Path = self.get_file_path_from_hash(
                file_hash, backup_repo_path
            )
        except ValueError as why:
            raise RuntimeError(
                f"Provided hash does not appear to be of proper length. Hash: "
                f"{CryptoHelper.bytes_to_hex(file_hash)}"
            ) from why

        # Ensure target folder exists.
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Open target.
        try:
            target_file: io.BufferedReader = target_path.open("wb+")
            source_file_manifest = source_file_manifest_path.open("r")
        except OSError as why:
            raise RuntimeError("Error opening file for backup restore.") from why

        # Ensure manifest version is of expected value.
        if source_file_manifest.readline() != "00\n":
            target_file.close()
            source_file_manifest.close()
            raise RuntimeError(
                f"File manifest is not of correct version. File: {file_hash}."
            )

        # Iterate over file manifest and restore file.
        for line in source_file_manifest:
            chunk_hash: bytes = CryptoHelper.b64_to_bytes(line)
            try:
                target_file.write(self.read_chunk(chunk_hash, backup_repo_path))
            except RuntimeError as why:
                target_file.close()
                source_file_manifest.close()
                raise RuntimeError(
                    f"Error restoring chunk with hash: {chunk_hash}."
                ) from why

        target_file.close()
        source_file_manifest.close()

    def read_chunk(self, chunk_hash: bytes, repo_path: Path) -> bytes:
        """
        Reads data out of a data chunk. Set for version 00 chunks. Does not currently
        handle encryption.

        Args:
            chunk_hash: Hash of chunk to get out of storage.
            repo_path: Path to the backup repository.

        Returns: Data in chunk.

        """
        # Get chunk path.
        chunk_path: Path = self.get_chunk_path_from_hash(chunk_hash, repo_path)

        # Attempt to read chunk
        try:
            chunk_file: io.BufferedReader = chunk_path.open("rb")
        except OSError as why:
            raise RuntimeError(
                f"Unable to read chunk with hash "
                f"{CryptoHelper.bytes_to_hex(chunk_hash)}."
            ) from why

        # confirm version byte is expected value.
        version: bytes = chunk_file.read(1)
        if version != bytes.fromhex("00"):
            chunk_file.close()
            raise RuntimeError(
                f"Chunk is of unexpected version. Unable to read. Version was "
                f"{CryptoHelper.bytes_to_hex(version)}."
            )

        # Read encryption byte and none. Code not currently used.
        # One byte for use encryption byte and 12 bytes of nonce.from
        _ = chunk_file.read(13)

        # Read compression byte.
        use_compression_byte: bytes = chunk_file.read(1)

        chunk_data: bytes = chunk_file.read()

        if use_compression_byte == self.BYTE_TRUE:
            try:
                chunk_data = self.zlib_decompress_bytes(chunk_data)
            except zlib.error as why:
                raise RuntimeError(
                    f"Unable to decompress chunk with hash: "
                    f"{CryptoHelper.bytes_to_hex(chunk_hash)}."
                ) from why

        return chunk_data

    @staticmethod
    def zlib_compress_bytes(bytes_to_compress: bytes) -> bytes:
        """
        Compress given bytes with zlib.

        Args:
            bytes_to_compress: Bytes to compress.

        Return: Compressed bytes.

        """
        return zlib.compress(bytes_to_compress)

    @staticmethod
    def zlib_decompress_bytes(bytes_to_decompress: bytes) -> bytes:
        """
        Decompress given bytes with zlib. Can throw zlib.error if bytes are not zlib
        compressed bytes.

        Args:
            bytes_to_decompress: Bytes to decompress.

        Returns: Decompressed bytes.

        """
        return zlib.decompress(bytes_to_decompress)

    @staticmethod
    def get_dir_size(server_path):
        """Recursively calculates dir size. Returns size in bytes. Must calculate human
        readable based on returned data

        Args:
            server_path (str): Path to calculate size

        Returns:
            _type_: Integer
        """
        # because this is a recursive function, we will return bytes,
        # and set human readable later
        total = 0
        for entry in os.scandir(server_path):
            if entry.is_dir(follow_symlinks=False):
                total += FileHelpers.get_dir_size(entry.path)
            else:
                total += entry.stat(follow_symlinks=False).st_size
        return total

    @staticmethod
    def get_drive_free_space(file_location: Path):
        _total, _used, free = shutil.disk_usage(file_location)
        return free

    @staticmethod
    def has_enough_storage(target_size: float, target_free_storage: float):
        if target_size > target_free_storage:
            return False
        return True
