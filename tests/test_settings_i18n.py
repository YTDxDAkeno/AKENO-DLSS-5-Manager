from akeno.core.settings import DEFAULTS, Settings


def test_settings_persist_and_factory_reset(db):
    s = Settings(db)
    assert s.get("target_fps") == DEFAULTS["target_fps"]
    s.set("target_fps", 90)
    s.set("language", "de")
    assert Settings(db).get("target_fps") == 90
    db.add_benchmark("g", "baseline", {"stats": {}})
    s.factory_reset()
    assert s.get("target_fps") == DEFAULTS["target_fps"] and s.get("language") == "auto"
    assert db.benchmarks("g"), "factory reset must not delete records needed to restore games"


def test_profiles_crud(db):
    db.save_profile("g", "quality", "optiscaler_nr", {"DlssNr.WorkingScale": 0.67}, active=True)
    db.save_profile("g", "custom", "optiscaler_nr", {"DlssNr.WorkingScale": 0.5}, active=True)
    assert db.active_profile("g", "optiscaler_nr")["name"] == "custom"
    assert len(db.profiles("g")) == 2
    db.delete_profile("g", "custom", "optiscaler_nr")
    assert db.active_profile("g", "optiscaler_nr") is None


def test_language_switching():
    from akeno import i18n
    i18n.set_language("de")
    assert i18n.t("Dashboard") == "Übersicht"
    assert i18n.t("Not measured yet — run a benchmark to see performance results.").startswith("Noch nicht gemessen")
    i18n.set_language("en")
    assert i18n.t("Dashboard") == "Dashboard"
    assert i18n.t("Some untranslated text") == "Some untranslated text"
    assert i18n.resolve("auto") in ("en", "de")
