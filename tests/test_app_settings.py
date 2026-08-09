"""AppSettings typed store and process singleton."""

from __future__ import annotations

from src.services.app_settings import Keys, make_ini_settings, set_app_settings


def test_typed_bool_int_str(tmp_path, qapp):
    ini = str(tmp_path / "s.ini")
    s = make_ini_settings(ini)
    s.set_bool(Keys.UI_COPY_PATH_AFTER, True)
    s.set_int(Keys.UI_TAB, 1)
    s.set_str(Keys.PLAYER_MODE, "system")
    s.sync()

    s2 = make_ini_settings(ini)
    assert s2.get_bool(Keys.UI_COPY_PATH_AFTER, False) is True
    assert s2.get_int(Keys.UI_TAB, 0) == 1
    assert s2.get_str(Keys.PLAYER_MODE, "") == "system"


def test_bool_string_coercion(tmp_path, qapp):
    ini = str(tmp_path / "b.ini")
    s = make_ini_settings(ini)
    s.qsettings.setValue("x/flag", "false")
    assert s.get_bool("x/flag", True) is False
    s.qsettings.setValue("x/flag", "true")
    assert s.get_bool("x/flag", False) is True


def test_set_app_settings_for_tests(tmp_path, qapp):
    ini = str(tmp_path / "p.ini")
    s = make_ini_settings(ini)
    set_app_settings(s)
    try:
        from src.services.app_settings import get_app_settings

        assert get_app_settings() is s
        s.set_str("test/key", "ok")
        assert get_app_settings().get_str("test/key") == "ok"
    finally:
        set_app_settings(None)
