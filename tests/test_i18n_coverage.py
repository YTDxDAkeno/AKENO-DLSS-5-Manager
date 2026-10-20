import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_every_ui_string_has_a_german_translation():
    import i18n_extract
    de = json.loads((ROOT / "akeno" / "data" / "i18n_de.json").read_text(encoding="utf-8"))
    missing = sorted(s for s in i18n_extract.needed() if s not in de)
    assert not missing, f"{len(missing)} strings without German translation, e.g. {missing[:5]}"


def test_translations_keep_placeholders():
    de = json.loads((ROOT / "akeno" / "data" / "i18n_de.json").read_text(encoding="utf-8"))
    for en, gde in de.items():
        # placeholders including conversions/format specs, e.g. {vram:.0f}, must survive translation exactly
        assert sorted(re.findall(r"\{[^{}]+\}", en)) == sorted(re.findall(r"\{[^{}]+\}", gde)), en


def test_dynamic_messages_render_fully_in_german():
    from akeno import i18n
    from akeno.i18n import msg, t
    i18n.set_language("de")
    try:
        m = msg("{label} is recommended for this game.", label=msg("Quality"))
        assert t(m) == "Qualität wird für dieses Spiel empfohlen."
        v = msg("{vram:.0f} GB VRAM: enough for one model pass at any working resolution.", vram=31.8)
        assert t(v).startswith("32 GB VRAM: genug")
        assert str(m) == "Quality is recommended for this game.", "the str value stays English for logs"
    finally:
        i18n.set_language("en")
