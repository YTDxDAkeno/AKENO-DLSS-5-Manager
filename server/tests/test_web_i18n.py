"""Every text the websites can show has a German translation with the same placeholders."""

import json
import re
from pathlib import Path

from akeno_server.web import i18n


def test_every_website_string_has_german():
    de = json.loads((Path(i18n.__file__).parent / "de.json").read_text(encoding="utf-8"))
    missing = sorted(s for s in i18n.needed() if s not in de)
    assert not missing, f"{len(missing)} website strings without German, e.g. {missing[:8]}"
    for en, text in de.items():
        assert set(re.findall(r"\{\w+\}", en)) == set(re.findall(r"\{\w+\}", text)), en


def test_mail_texts_exist_in_both_languages():
    from akeno_server.mail import TEXTS
    for kind, langs in TEXTS.items():
        assert set(langs) == {"en", "de"}, kind
