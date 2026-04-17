from peewee import SqliteDatabase

from app.classes.models.roles import HelperRoles, Roles


def test_update_role_sets_last_update_in_sqlite_without_format_change() -> None:
    """Create an in-memory database with a known role. Confirms new update role behavior
    functions correctly without needing a database migration."""
    # Spin up temporary, in-memory, database.
    db = SqliteDatabase(":memory:")

    # Connect to db
    with db.bind_ctx([Roles]):
        db.connect()
        db.create_tables([Roles])

        # Insert known role.
        role_id = Roles.insert(
            {
                Roles.role_name: "test-role",
                Roles.last_update: "03/15/2026, 12:34:56",
            }
        ).execute()

        # Update role with function that we are interested in.
        HelperRoles.update_role(role_id, {"manager": None})
        role = Roles.get_by_id(role_id)

        raw_last_update = db.execute_sql(
            "select last_update from roles where role_id = ?", (role_id,)
        ).fetchone()[0]

        # Confirm shape of role without knowing current time. A bit limited.
        # SQLite default time format is in the shape: 2024-02-22 22:42:41
        # Funny tangent: This means that depending on the timezone used, sqlite default
        # time formatting can not correctly distinguish some daylight savings related
        # time changes. At some point I should investigate if that can cause some issues
        # in Crafty. It would be one hell of an edge-case but very funny.
        assert isinstance(role.last_update, str)
        assert role.last_update == raw_last_update
        assert len(role.last_update) == 20
        assert role.last_update[2] == "/"
        assert role.last_update[5] == "/"
        assert role.last_update[10:12] == ", "

        db.close()
