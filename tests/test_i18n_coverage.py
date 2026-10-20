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
        assert set(re.findall(r"\{\w+\}", en)) == set(re.findall(r"\{\w+\}", gde)), en
