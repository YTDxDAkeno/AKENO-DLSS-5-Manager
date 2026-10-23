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
    from akeno.ui.pages import hardware, install
    out |= {x for _, n, d in support.FEATURES for x in (n, d)}
    out |= set(hardware.R_LABEL.values()) | {title for _, title in install.INSTALL_STEPS}
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
    out |= {"Community DLSS 5", "Versions", "Advanced / Diagnostics", "Choose Preset", "Restore previous version", "Remove mod completely",
        "Find local runtimes", "Import community runtime", "View installation logs", "Import private component manifest", "Rendering pipelines", "Community runtime diagnostics",
        "Pure 0", "Pure 1", "Pure 2", "Performance", "Balanced", "Quality", "Ultra", "Disabled"}
    for path in [ROOT/"akeno/mods/service.py", ROOT/"akeno/fivem/community_stack.py"]:
        tree=ast.parse(path.read_text())
        for call in ast.walk(tree):
            if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=="need":
                for arg in call.args[3:5]:
                    if isinstance(arg,ast.Constant) and isinstance(arg.value,str):out.add(arg.value)
    for role in ('reshade','motion_provider','neural_consumer'):
        out.add('A compatible '+role+' is required.')
    from akeno.mods.catalog import CommunityCatalog
    out |= {r['name'] for r in CommunityCatalog.bundled().recipes.values()}
    return out


def needed() -> set[str]:
    return literal_calls() | data_strings()


if __name__ == "__main__":
    import json
    de = json.loads((ROOT / "akeno" / "data" / "i18n_de.json").read_text(encoding="utf-8")) \
        if (ROOT / "akeno" / "data" / "i18n_de.json").exists() else {}
    missing = sorted(s for s in needed() if s not in de)
    print(json.dumps(missing, indent=1, ensure_ascii=False))
    print(len(missing), "missing", file=sys.stderr)
