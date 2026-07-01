import os
import subprocess
import sys
from pathlib import Path

import peewee
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def isolated_crafty_env(tmp_path):
    config_dir = tmp_path / "app" / "config"
    db_dir = config_dir / "db"
    migrations_status = tmp_path / "app" / "migrations" / "status"
    config_dir.mkdir(parents=True)
    db_dir.mkdir(parents=True)
    migrations_status.mkdir(parents=True)

    env = os.environ.copy()
    env["CRAFTY_ALLOW_ADMIN"] = "1"
    env["PYTHONPATH"] = str(ROOT)

    yield tmp_path, env

    session_lock = config_dir / "session.lock"
    if session_lock.exists():
        session_lock.unlink(missing_ok=True)


def test_migrations_apply_on_empty_database(isolated_crafty_env):
    tmp_path, env = isolated_crafty_env
    db_path = tmp_path / "app" / "config" / "db" / "crafty.sqlite"

    script = f"""
import os
import sys
sys.path.insert(0, {str(ROOT)!r})
os.chdir({str(ROOT)!r})

import peewee
from app.classes.helpers.helpers import Helpers
from app.classes.shared.migration import MigrationManager

helper = Helpers()
helper.root_dir = {str(tmp_path)!r}
helper.settings_file = os.path.join(helper.root_dir, "app", "config", "config.json")
helper.db_path = {str(db_path)!r}
helper.migration_dir = os.path.join({str(ROOT)!r}, "app", "migrations")

database = peewee.SqliteDatabase(helper.db_path, pragmas={{"journal_mode": "wal"}})
database.connect()
manager = MigrationManager(database, helper)
manager.up()
database.close()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    database = peewee.SqliteDatabase(str(db_path))
    database.connect()
    user_columns = {column.name for column in database.get_columns("users")}
    server_columns = {column.name for column in database.get_columns("servers")}
    database.close()

    assert "hints" in user_columns
    assert "show_status" in server_columns


def test_fresh_install_bootstraps_admin_user(isolated_crafty_env):
    tmp_path, env = isolated_crafty_env
    db_path = tmp_path / "app" / "config" / "db" / "crafty.sqlite"

    script = f"""
import os
import sys
sys.path.insert(0, {str(ROOT)!r})
os.chdir({str(ROOT)!r})

import peewee
from app.classes.helpers.helpers import Helpers
from app.classes.shared.migration import MigrationManager
from app.classes.shared.main_models import DatabaseBuilder
from app.classes.models.users import HelperUsers, Users
from app.classes.models.management import HelpersManagement
from app.classes.models.base_model import database_proxy

helper = Helpers()
helper.root_dir = {str(tmp_path)!r}
helper.settings_file = os.path.join(helper.root_dir, "app", "config", "config.json")
helper.db_path = {str(db_path)!r}
helper.migration_dir = os.path.join({str(ROOT)!r}, "app", "migrations")

database = peewee.SqliteDatabase(helper.db_path, pragmas={{"journal_mode": "wal"}})
database_proxy.initialize(database)
Users._meta.database = database

MigrationManager(database, helper).up()

user_helper = HelperUsers(database, helper)
management_helper = HelpersManagement(database, helper)
installer = DatabaseBuilder(database, helper, user_helper, management_helper)
assert installer.is_fresh_install()
installer.default_settings("crafty-test-pass")
print(user_helper.get_user_total())
database.close()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip().splitlines()[-1].strip() == "1"
