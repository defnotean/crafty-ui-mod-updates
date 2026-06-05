import logging
import os
import json

from jsonschema import validate
from jsonschema.exceptions import ValidationError

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)

reset_schema = {
    "type": "object",
    "properties": {
        "dimension": {"type": "string", "enum": ["overworld", "nether", "end"]},
    },
    "required": ["dimension"],
    "additionalProperties": False,
}


class ApiServersServerWorldResetHandler(BaseApiHandler):
    """POST -> regenerate a single dimension (overworld / nether / end), keeping
    player inventories and the other dimensions intact. Backs up first."""

    def post(self, server_id):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        permissions = self.controller.server_perms.get_permissions(mask)
        if (
            EnumPermissionsServer.BACKUP not in permissions
            or EnumPermissionsServer.COMMANDS not in permissions
        ):
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )

        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, reset_schema)
        except ValidationError as why:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": str(why.message),
                },
            )

        dimension = data["dimension"]

        # Ensure a backup config exists so the pre-reset backup can run.
        try:
            if not self.controller.management.get_backups_by_server(server_id, True):
                self.controller.management.add_default_backup_config(
                    server_id, os.path.join(self.helper.backup_path, server_id)
                )
        except Exception as e:
            logger.warning("world reset: could not ensure backup config: %s", e)

        server_instance = self.controller.servers.get_server_instance_by_id(server_id)
        server_instance.reset_dimension(dimension)

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"reset the {dimension} for server {server_id}",
            server_id,
            self.get_remote_ip(),
        )

        return self.finish_json(
            200, {"status": "ok", "data": {"dimension": dimension, "queued": True}}
        )
