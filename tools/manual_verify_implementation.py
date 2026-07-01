#!/usr/bin/env python3
"""Manual verification script for fork implementation changes."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def ok(name: str) -> None:
    print(f"  OK  {name}")


def fail(name: str, detail: str) -> None:
    FAILURES.append(f"{name}: {detail}")
    print(f"  FAIL {name}: {detail}")


def check_migration_notifications_path() -> None:
    import main

    with tempfile.TemporaryDirectory() as tmp:
        status_dir = Path(tmp) / "app" / "migrations" / "status"
        status_dir.mkdir(parents=True)
        (status_dir / "test.json").write_text(
            json.dumps({"feature_x": {"status": False, "message": "needs attention"}}),
            encoding="utf-8",
        )
        original_path = main.APPLICATION_PATH
        try:
            main.APPLICATION_PATH = tmp
            notes = main.get_migration_notifications()
        finally:
            main.APPLICATION_PATH = original_path
    if notes == ["feature_x"]:
        ok("get_migration_notifications reads status files via joined path")
    else:
        fail("get_migration_notifications", f"expected ['feature_x'], got {notes}")


def check_tunnel_manager_env() -> None:
    from app.classes.shared.tunnel_manager import TunnelManager

    with patch.dict(os.environ, {}, clear=True):
        import importlib
        import app.classes.shared.tunnel_manager as tm

        importlib.reload(tm)
        if not tm.TunnelManager.is_available():
            ok("tunnel is_available() false when env unset")
        else:
            fail("tunnel is_available", "expected False with empty env")

    with patch.dict(
        os.environ,
        {
            "CRAFTY_BORE_NODE": __file__,
            "CRAFTY_BORE_CLIENT": __file__,
            "CRAFTY_BORE_HOST": "bore.pub",
        },
        clear=True,
    ):
        importlib.reload(tm)
        if tm.TunnelManager.is_available():
            ok("tunnel is_available() true when env + files exist")
        else:
            fail("tunnel is_available", "expected True with valid env pointing at this script")

        mgr = tm.TunnelManager()
        result = mgr.start("srv-1", 25565)
        if result.get("error"):
            ok(f"tunnel start returns error when bore client cannot run: {result['error'][:60]}...")
        else:
            fail("tunnel start", f"expected error, got {result}")


def check_i18n_completeness() -> None:
    """Ensure every craftyMods translate() key in server_mods.html exists in en_EN.json."""
    import re

    html = (ROOT / "app/frontend/templates/panel/server_mods.html").read_text(encoding="utf-8")
    en = json.loads((ROOT / "app/translations/en_EN.json").read_text(encoding="utf-8"))
    keys = re.findall(r"translate\('craftyMods',\s*'([^']+)'", html)
    missing = [k for k in set(keys) if k not in en.get("craftyMods", {})]
    if not missing:
        ok(f"all {len(set(keys))} craftyMods template keys present in en_EN.json")
    else:
        fail("i18n completeness", f"missing keys: {missing}")

    en = json.loads((ROOT / "app/translations/en_EN.json").read_text(encoding="utf-8"))
    required = {
        ("craftyMods", "pageTitle"),
        ("craftyMods", "scanAria"),
        ("craftyMods", "pullUpdatesAria"),
        ("craftyDiscover", "navTitle"),
        ("craftyDocs", "navTitle"),
        ("serverSchedules", "autoRestartCalloutTitle"),
        ("serverSchedules", "autoRestartCalloutLink"),
        ("serverConfig", "serverAutoRestart"),
    }
    for section, key in required:
        if section in en and key in en[section]:
            ok(f"i18n en_EN {section}.{key}")
        else:
            fail("i18n", f"missing {section}.{key}")


def check_static_assets() -> None:
    css = ROOT / "app/frontend/static/assets/css/internal/crafty-mods.css"
    if css.is_file() and css.stat().st_size > 500:
        ok(f"crafty-mods.css exists ({css.stat().st_size} bytes)")
    else:
        fail("crafty-mods.css", "missing or too small")

    html = (ROOT / "app/frontend/templates/panel/server_mods.html").read_text(encoding="utf-8")
    if "/static/assets/css/internal/crafty-mods.css" in html:
        ok("server_mods.html links internal/crafty-mods.css")
    else:
        fail("server_mods.html", "CSS link path wrong")

    schedules = (ROOT / "app/frontend/templates/panel/server_schedules.html").read_text(
        encoding="utf-8"
    )
    if "Automatic Restart" in schedules and "POST" in schedules and "/tasks/" in schedules:
        # old duplicate UI used POST to /tasks/
        if 'name: "Automatic Restart"' in schedules or "Automatic Restart" in schedules and "saveAutoRestart" in schedules:
            fail("server_schedules.html", "legacy auto-restart task UI still present")
    if "autoRestartCalloutTitle" in schedules or "auto-restart-callout" in schedules:
        ok("server_schedules.html uses callout instead of duplicate task UI")
    else:
        fail("server_schedules.html", "callout missing")


def check_duplicate_migrations_removed() -> None:
    migrations = ROOT / "app/migrations"
    copies = list(migrations.glob("* copy.py"))
    if not copies:
        ok("duplicate migration copy files removed")
    else:
        fail("migrations", f"still present: {copies}")


def check_mod_update_batch_endpoint() -> None:
    source = (ROOT / "app/classes/shared/mod_update_manager.py").read_text(encoding="utf-8")
    if "/version_files/update" in source and "/version_file/" not in source.replace(
        "/version_files/update", ""
    ):
        ok("ModUpdateManager uses batch /version_files/update endpoint")
    elif "/version_files/update" in source:
        ok("ModUpdateManager references batch /version_files/update endpoint")
    else:
        fail("mod_update_manager", "batch endpoint not found")


def check_mod_updater_delegates() -> None:
    from app.classes.shared.mod_updater import ModrinthModUpdater

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / "mods").mkdir()
        fake = MagicMock()
        fake.update_available.return_value = {
            "mods": [],
            "summary": {"installed": 0, "updated": 0},
        }
        with patch(
            "app.classes.shared.mod_updater.ModUpdateManager", return_value=fake
        ) as mock_mgr:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                updater = ModrinthModUpdater()
                result = updater.update(str(path), ["fabric"], ["1.21.1"])
            if mock_mgr.called and result.checked == 0:
                ok("ModrinthModUpdater delegates to ModUpdateManager.update_available")
            else:
                fail("ModrinthModUpdater", "delegation or result mapping failed")


def main() -> int:
    print("Manual verification checks\n" + "=" * 40)
    check_migration_notifications_path()
    check_tunnel_manager_env()
    check_i18n_completeness()
    check_static_assets()
    check_duplicate_migrations_removed()
    check_mod_update_batch_endpoint()
    check_mod_updater_delegates()
    print("=" * 40)
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s):")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("\nAll manual checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
