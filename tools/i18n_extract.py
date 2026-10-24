"""List UI strings that need a German translation.

Collects literal arguments of ``t("...")`` and ``msg("...")`` calls plus the static display strings that reach ``t()``
through data (profile labels, parameter help, status names). Used by tests/test_i18n_coverage.py.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def literal_calls() -> set[str]:
    out: set[str] = set()
    for path in (ROOT / "akeno").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "msg") \
                    and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    out.add(a.value)
    return out


def data_strings() -> set[str]:
    sys.path.insert(0, str(ROOT))
    from akeno.backends.base import default_registry
    from akeno.core.models import Game, GraphicsApi
    from akeno.ui.pages import backups, dashboard, library
    from akeno.ui.main_window import NAV
    out: set[str] = set()
    for a in default_registry().all():
        for api in (GraphicsApi.D3D12, GraphicsApi.D3D11):
            g = Game(id="x", name="x", install_dir="x", launcher="manual", api=api)
            for p in a.profiles(g):
                out |= {p.label, p.summary, p.tradeoff}
        for s in a.param_specs():
            out |= {s.label, s.help} | {c[1] for c in s.choices}
    out |= {v[0] for v in dashboard.SUPPORT_TEXT.values()} | {v[0] for v in dashboard.COMPAT_TEXT.values()}
    out |= set(library.LAUNCHERS.values()) | set(backups.STATE.values()) | {n[1] for n in NAV}
    out |= {a.label for a in GraphicsApi}
    from akeno.hardware import support
    from akeno.ui.pages import install
    out |= {x for _, n, d in support.FEATURES for x in (n, d)}
    out |= {title for _, title in install.INSTALL_STEPS}
    from akeno.fivem.messages import LABELS
    out |= set(LABELS.values())
    from akeno.community.runtime import LABELS as COMMUNITY_LABELS
    out |= set(COMMUNITY_LABELS.values())
    from akeno.pipeline.recipes import RECIPES
    from akeno.pipeline.results import RouteState
    out |= {r.name for r in RECIPES} | {s.value for s in RouteState}
    out |= {"Choose pipeline", "Runtime self-test", "Official DLL management", "Visual A/B comparison",
            "Analyze pipelines", "Preview installation", "Open game verification", "Choose NR-off screenshot", "Choose NR-on screenshot",
            "Offline / singleplayer development", "Authorized server assessment", "Unsupported / blocked session",
            "Camera / scene", "Exact component version", "Upstream HTTPS source", "Component license"}
    out |= {"System", "Game", "Dependencies", "Installation"}
    out |= {"good", "fair", "poor", "Good", "Fair", "Poor", "low", "medium", "high", "bundled", "downloaded",
            "measured", "estimated", "hardware only"}
    out |= {"Performance", "Balanced", "Quality", "Ultra", "Disabled"}
    out |= stack_strings()
    out |= suite_strings()
    return out


def suite_strings() -> set[str]:
    """Licensing and suite texts that reach t() through tables (status names, error codes, page titles)."""
    from akeno.performance import tweaks
    from akeno.ui import game_hub
    from akeno.ui.pages import benchmark, booster
    perf = set(tweaks.PRIORITY_LABELS.values()) | set(tweaks.GpuPreference.LABELS.values())
    perf |= {text for _, text in booster.MODES} | set(booster.SCOPE.values()) | set(benchmark.VERDICTS.values())
    for title, choices in booster.CUSTOM_CHOICES.values():
        perf |= {title} | {text for _, text in choices}
    perf |= {c for c in game_hub.CATEGORIES if c}
    from akeno.licensing.manager import STATUS_TEXT, TYPE_NAMES
    from akeno.ui import account_ui, license_gate
    from akeno.ui.main_window import SECTIONS
    from akeno.ui.pages.account import EVENT_NAMES
    out = perf | set(STATUS_TEXT.values()) | set(TYPE_NAMES.values()) | set(account_ui.ERRORS.values())
    out |= set(account_ui.STATUS_NAME.values()) | set(EVENT_NAMES.values())
    out |= {x for pair in license_gate.LicenseGate.TITLES.values() for x in pair if x}
    out |= {license_gate.SUITE_NAME, license_gate.ERR_SHORT} | set(license_gate.FEATURES)
    for section, items in SECTIONS:
        out.add(section)
        out |= {item[2] for item in items if item[0] == "group"}
    out |= {"The license server address must start with https://", "The license server address is incomplete."}
    out |= desktop_strings()
    return out


def desktop_strings() -> set[str]:
    """Desktop Studio texts that reach t() through tables (widget names, weather, days, page choices)."""
    from akeno.desktop import widgets
    from akeno.ui.pages import desktop
    out = set(widgets.TITLES.values()) | set(widgets.WEATHER.values()) | {"Weather"}
    out |= set(widgets.WEEKDAYS) | set(widgets.DAY_SHORT) | set(widgets.MONTHS)
    for table in (desktop.KIND_TEXT, desktop.MODE_TEXT, desktop.FIT_TEXT, desktop.EDGE_TEXT, desktop.STATE_TEXT,
                  desktop.PAUSE_TEXT):
        out |= set(table.values())
    out.add(desktop.MEDIA_FILTER)
    from akeno.sync.engine import CATEGORIES
    from akeno.ui.pages import online
    out |= set(CATEGORIES.values()) | set(online.KIND_TEXT.values()) | {text for _, text in online.EXPORTS}
    out |= {"Settings", "Desktop"}
    from akeno.ui import notifications
    from akeno.ui.pages import updates
    out |= set(notifications.CATEGORIES.values()) | set(updates.CHANNEL_TEXT.values())
    out |= {"Updates install into the built program only (this copy runs from the source code).",
            "This build has no release verification key: download updates from the release page."}
    return out


STACK_CALLS = {"Issue": (2, 3), "StackError": (0, 1), "MissingDependency": (0, 1), "PreviewRow": (2,),
               "GameRunning": (0,)}


def stack_strings() -> set[str]:
    """Strings of the stack engine and its pages that reach t() through data or exception objects."""
    out: set[str] = set()
    for path in (ROOT / "akeno" / "stack").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in STACK_CALLS:
                for i in STACK_CALLS[node.func.id]:
                    if i < len(node.args) and isinstance(node.args[i], ast.Constant) and isinstance(node.args[i].value, str):
                        out.add(node.args[i].value)
                for kw in node.keywords:
                    if kw.arg in ("message", "remedy", "detail") and isinstance(kw.value, ast.Constant):
                        out.add(kw.value.value)
            # constant reasons handed to disable_file(path, "...") in the planner
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "disable_file":
                for a in node.args[1:]:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        out.add(a.value)
                    elif isinstance(a, ast.IfExp):
                        out |= {x.value for x in (a.body, a.orelse) if isinstance(x, ast.Constant)}
    from akeno.stack import configs
    from akeno.stack.catalog import ROLES, StackCatalog
    from akeno.ui import stack_panel
    from akeno.ui.pages import advanced, fivem
    out |= set(ROLES.values()) | set(configs.PRESET_LABELS.values())
    out |= set(stack_panel.CHANNEL.values()) | set(stack_panel.ACTION_TEXT.values()) | set(stack_panel.LAUNCHERS.values())
    out |= set(fivem.KIND_TEXT.values()) | {title for _, title in advanced.TABS}
    for spec in configs.RENODX_SETTINGS + configs.optiscaler_settings(frozenset({"presr", "cache", "passes"}),
                                                                       "d3d12", (), False):
        out |= {spec.label, spec.help} | {c[1] for c in spec.choices}
    out.discard("")
    cat = StackCatalog.load()
    for r in cat.recipes.values():
        out |= {r.name, r.summary, *r.details}
    for g in cat.games:
        out |= {g.name, *g.notes}
    for c in cat.components.values():
        out.add(c.description)
        for v in c.versions:
            out |= set(v.notes)
    return {x for x in out if x}


def needed() -> set[str]:
    return literal_calls() | data_strings()


if __name__ == "__main__":
    import json
    de = json.loads((ROOT / "akeno" / "data" / "i18n_de.json").read_text(encoding="utf-8")) \
        if (ROOT / "akeno" / "data" / "i18n_de.json").exists() else {}
    missing = sorted(s for s in needed() if s not in de)
    print(json.dumps(missing, indent=1, ensure_ascii=False))
    print(len(missing), "missing", file=sys.stderr)
