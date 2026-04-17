import os
import uuid
import json
import time
import shlex
import pathlib
import logging
import threading
import subprocess
from pathlib import PurePosixPath, Path

from app.classes.big_bucket.bigbucket import BigBucket
from app.classes.big_bucket.hytale import HytaleJSON
from app.classes.controllers.server_perms_controller import PermissionsServers
from app.classes.controllers.servers_controller import ServersController
from app.classes.helpers.helpers import Helpers
from app.classes.helpers.file_helpers import FileHelpers
from app.classes.shared.websocket_manager import WebSocketManager
from app.classes.steamcmd.steamcmd import SteamCMD

logger = logging.getLogger(__name__)

HYTALE_0UTPUT_NAME = "hytale.zip"


class ImportHelpers:
    allowed_quotes = ['"', "'", "`"]

    def __init__(self, helper, file_helper):
        self.file_helper: FileHelpers = file_helper
        self.helper: Helpers = helper
        self.big_bucket = BigBucket(helper)

    def import_zipped_server(
        self,
        archive_path,
        new_server_dir,
        base_include_path,
        port,
        new_id,
        server_type,
        full_exe_path=None,
    ):
        import_thread = threading.Thread(
            target=self.import_threaded_zipped_server,
            daemon=True,
            args=(
                archive_path,
                new_server_dir,
                base_include_path,
                port,
                new_id,
                server_type,
                full_exe_path,
            ),
            name=f"{new_id}_import",
        )
        import_thread.start()

    def import_threaded_zipped_server(
        self,
        archive_path,
        new_server_dir,
        base_include_path,
        port,
        new_id,
        server_type,
        full_exe_path,
    ):
        self.file_helper.unzip_file(
            archive_path,
            new_server_dir,
            new_id,
            False,
            base_include_path=base_include_path,
        )

        time.sleep(2)
        if (
            not self.helper.is_os_windows() and full_exe_path
        ):  # we only expect full jar path for bedrock
            if Helpers.check_file_exists(full_exe_path):
                os.chmod(full_exe_path, 0o2760)  # apply execute permissions

        self.file_helper.del_file(archive_path)

        has_properties = False
        for item in os.listdir(new_server_dir):
            if str(item) == "server.properties":
                has_properties = True
        if not has_properties and "minecraft" in server_type:
            logger.info(
                f"No server.properties found on zip file import. "
                f"Creating one with port selection of {str(port)}"
            )
            with open(
                os.path.join(new_server_dir, "server.properties"), "w", encoding="utf-8"
            ) as file:
                file.write(f"server-port={port}")
                file.close()
        time.sleep(5)
        ServersController.finish_import(new_id)
        server_users = PermissionsServers.get_server_user_list(new_id)
        for user in server_users:
            WebSocketManager().broadcast_user(user, "send_start_reload", {})

    def download_steam_server(self, app_id, server_id, server_dir, server_exe):
        download_thread = threading.Thread(
            target=self.create_steam_server,
            daemon=True,
            args=(app_id, server_id, server_dir, server_exe),
            name=f"{server_id}_download",
        )
        download_thread.start()

    def create_steam_server(self, app_id, server_id, server_dir, server_exe):
        if not server_exe:
            server_exe = "game.exe"  # replace with actual exe eventually

        # Initiate SteamCMD & game installing status.
        ServersController.set_import(server_id)

        # Set our storage locations
        steamcmd_path = os.path.join(server_dir, "steamcmd_files")
        gamefiles_path = os.path.join(server_dir, "gameserver_files")

        # Ensure game and steam directories exist in server directory.
        self.helper.ensure_dir_exists(steamcmd_path)
        self.helper.ensure_dir_exists(gamefiles_path)

        # Initialize SteamCMD
        self.steam = SteamCMD(steamcmd_path)

        # Install SteamCMD for managing game server files.
        self.steam.install()

        # Install the game server files.
        self.steam.app_update(app_id, gamefiles_path)

        # Set the server execuion command. TODO brainstorm how to approach.
        full_exe_path = os.path.join(steamcmd_path, server_exe)
        if Helpers.is_os_windows():
            server_command = f'"{full_exe_path}"'
        else:
            server_command = f"./{server_exe}"
        logger.debug("command: " + server_command)

        # Finalise SteamCMD & game installing status.
        ServersController.finish_import(server_id)
        server_users = PermissionsServers.get_server_user_list(server_id)
        for user in server_users:
            WebSocketManager().broadcast_user(user, "send_start_reload", {})

    def download_bedrock_server(self, path, new_id):
        bedrock_url = Helpers.get_latest_bedrock_url()
        download_thread = threading.Thread(
            target=self.download_threaded_bedrock_server,
            daemon=True,
            args=(path, new_id, bedrock_url),
            name=f"{new_id}_download",
        )
        download_thread.start()

    def download_threaded_bedrock_server(
        self, path, new_id, bedrock_url, server_update=False
    ):
        """
        Downloads the latest Bedrock server, unzips it, sets necessary permissions.

        Parameters:
            path (str): The directory path to download and unzip the Bedrock server.
            new_id (str): The identifier for the new server import operation.

        This method handles exceptions and logs errors for each step of the process.
        """
        try:
            if bedrock_url:
                file_path = os.path.join(path, "bedrock_server.zip")
                success = FileHelpers.ssl_get_file(
                    bedrock_url, path, "bedrock_server.zip"
                )
                if not success:
                    logger.error("Failed to download the Bedrock server zip.")
                    return

                unzip_path = self.helper.wtol_path(file_path)
                destination_path = pathlib.Path(unzip_path).parents[0]
                # unzips archive that was downloaded.
                self.file_helper.unzip_file(
                    unzip_path, destination_path, new_id, server_update=server_update
                )
                # adjusts permissions for execution if os is not windows

                if not self.helper.is_os_windows():
                    os.chmod(os.path.join(path, "bedrock_server"), 0o0744)

                # we'll delete the zip we downloaded now
                os.remove(file_path)
            else:
                logger.error("Bedrock download URL issue!")
        except Exception as e:
            logger.critical(
                f"Failed to download bedrock executable during server creation! \n{e}"
            )
            raise e

        ServersController.finish_import(new_id)
        server_users = PermissionsServers.get_server_user_list(new_id)
        for user in server_users:
            WebSocketManager().broadcast_user(user, "send_start_reload", {})

    def download_install_threaded_hytale(self, path, new_id):
        download_thread = threading.Thread(
            target=self.download_install_hytale,
            daemon=True,
            args=(path, new_id),
            name=f"{new_id}_download",
        )
        download_thread.start()

    def download_install_hytale(self, server_path: str | Path, new_id: uuid.UUID):
        server_users = PermissionsServers.get_server_user_list(new_id)

        bb_cache = self.big_bucket.get_bucket_data(self.helper.big_bucket_hytale_cache)
        try:
            hytale_json = HytaleJSON(bb_cache)
            unix_exe = PurePosixPath(hytale_json.linux_installer_url).name
            windows_exe = PurePosixPath(hytale_json.windows_installer_url).name
        except KeyError:
            logger.error("Failed to create Hytale server with keyerror")
            ServersController.finish_import(new_id)
            return
        install_command = (
            f"./{unix_exe} "
            f"{hytale_json.commands.download_path_command} {HYTALE_0UTPUT_NAME}"
        )
        if self.helper.is_os_windows():
            install_command = (
                f"{server_path}/{windows_exe} "
                f"{hytale_json.commands.download_path_command} {HYTALE_0UTPUT_NAME}"
            )
            self.file_helper.ssl_get_file(
                hytale_json.windows_installer_url, server_path, windows_exe
            )
        else:
            self.file_helper.ssl_get_file(
                hytale_json.linux_installer_url, server_path, unix_exe
            )
            os.chmod(Path(server_path, unix_exe), 0o2760)  # set executable permissions
            install_command = shlex.split(install_command)
        process = subprocess.Popen(
            install_command,
            cwd=server_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        url_line = ""
        while process.poll() is None:
            line = process.stdout.readline().strip()
            if not line:
                continue

            line = line.strip()
            # TODO: Do not send data to clients who do not have permission to view
            # this server's console
            if len(WebSocketManager().clients) > 0:
                WebSocketManager().broadcast_page_params(
                    "/panel/server_detail",
                    {"id": new_id},
                    "vterm_new_line",
                    {"line": line + "<br />"},
                )
            if (
                line.startswith(hytale_json.parsing_lines.url_line_start)
                and url_line == ""
            ):
                url_line = line
                with open(
                    Path(server_path, "hytale_install_auth_url.txt"),
                    "w",
                    encoding="utf-8",
                ) as auth_file:
                    auth_file.write(url_line)
                for user in server_users:
                    WebSocketManager().broadcast_user(
                        user,
                        "hytale_auth",
                        {"link": line, "server_id": new_id},
                    )

        # Unzip downloaded archive.
        self.file_helper.unzip_file(
            Path(server_path, HYTALE_0UTPUT_NAME),
            server_path,
        )
        self.install_or_update_monitoring_plugins(new_id, server_path)
        ServersController.finish_import(new_id)
        for user in server_users:
            WebSocketManager().broadcast_user(user, "send_start_reload", {})

    def install_or_update_monitoring_plugins(
        self, server_id: uuid.UUID, server_path: str | Path
    ):
        bb_cache = self.big_bucket.get_bucket_data(self.helper.big_bucket_hytale_cache)
        try:
            hytale_json = HytaleJSON(bb_cache)
        except KeyError:
            logger.error("Failed to download hytale plugins with keyerror")
            return
        logger.info("Installing Nitrado Webserver Plugin to server %s", server_id)
        # make sure our mods dir exists before doing anything
        # Download webserver plugin required for query plugin
        self.helper.ensure_dir_exists(Path(server_path, "mods"))
        self.file_helper.ssl_get_file(
            hytale_json.plugins.webserver_plugin_url,
            Path(server_path, "mods"),
            "nitrado-webserver.jar",
        )
        # Download query plugin
        logger.info("Installing Nitrado Query Plugin to server %s", server_id)
        self.file_helper.ssl_get_file(
            hytale_json.plugins.query_plugin_url,
            Path(server_path, "mods"),
            "nitrado-query.jar",
        )
        self.modify_permissions_json(server_path)

    def modify_permissions_json(self, server_path: str | Path):
        # Make sure we do not overwrite user data
        if not Helpers.check_file_exists(str(Path(server_path, "permissions.json"))):
            with open(
                Path(server_path, "permissions.json"), "w", encoding="utf-8"
            ) as perms_file:
                decoded = {
                    "groups": {
                        "ANONYMOUS": [
                            "nitrado.query.web.read.server",
                            "nitrado.query.web.read.universe",
                            "nitrado.query.web.read.players",
                        ]
                    }
                }
                perms_file.write(json.dumps(decoded, indent=4))
