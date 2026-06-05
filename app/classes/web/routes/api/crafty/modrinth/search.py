import logging

from app.classes.web.base_api_handler import BaseApiHandler
from app.classes.shared.modrinth_manager import ModrinthManager

logger = logging.getLogger(__name__)

VALID_TYPES = {"modpack", "mod", "plugin", "datapack", "resourcepack", "shader"}


class ApiCraftyModrinthSearchHandler(BaseApiHandler):
    def get(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        query = self.get_query_argument("q", "")
        project_type = self.get_query_argument("type", "")
        loader = self.get_query_argument("loader", "")
        game_version = self.get_query_argument("game_version", "")
        limit = self.get_query_argument("limit", "20")
        offset = self.get_query_argument("offset", "0")
        index = self.get_query_argument("index", "relevance")

        if project_type and project_type not in VALID_TYPES:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "BAD_TYPE",
                    "error_data": "type must be one of: " + ", ".join(sorted(VALID_TYPES)),
                },
            )
        try:
            data = ModrinthManager().search(
                query=query,
                project_type=project_type or None,
                loaders=[loader] if loader else None,
                game_versions=[game_version] if game_version else None,
                index=index,
                limit=int(limit) if str(limit).isdigit() else 20,
                offset=int(offset) if str(offset).isdigit() else 0,
            )
            return self.finish_json(200, {"status": "ok", "data": data})
        except Exception as e:  # surfaced to the UI
            logger.warning("Modrinth search failed: %s", e)
            return self.finish_json(
                502,
                {"status": "error", "error": "MODRINTH_ERROR", "error_data": str(e)},
            )


class ApiCraftyModrinthVersionsHandler(BaseApiHandler):
    def get(self, project_id):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        loader = self.get_query_argument("loader", "")
        game_version = self.get_query_argument("game_version", "")
        try:
            mgr = ModrinthManager()
            project = mgr.project(project_id)
            versions = mgr.versions(
                project_id,
                loaders=[loader] if loader else None,
                game_versions=[game_version] if game_version else None,
            )
            return self.finish_json(
                200,
                {"status": "ok", "data": {"project": project, "versions": versions}},
            )
        except Exception as e:
            logger.warning("Modrinth versions failed: %s", e)
            return self.finish_json(
                502,
                {"status": "error", "error": "MODRINTH_ERROR", "error_data": str(e)},
            )
