import json
import os
import tempfile
from unittest.mock import patch

import main


def test_get_migration_notifications_uses_status_dir_path():
    with tempfile.TemporaryDirectory() as tmp:
        status_dir = os.path.join(tmp, "app", "migrations", "status")
        os.makedirs(status_dir)
        with open(os.path.join(status_dir, "feature.json"), "w", encoding="utf-8") as handle:
            json.dump({"needs_action": {"status": False}}, handle)

        with patch.object(main, "APPLICATION_PATH", tmp):
            notes = main.get_migration_notifications()

    assert notes == ["needs_action"]
