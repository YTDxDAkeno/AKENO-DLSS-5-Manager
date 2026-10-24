"""FPS Booster profiles: which tweaks a profile uses and how. ``custom`` takes the user's own choice."""

from __future__ import annotations

from dataclasses import dataclass, field

from akeno.i18n import msg


@dataclass(frozen=True)
class BoostProfile:
    id: str
    name: str
    summary: str
    options: dict = field(default_factory=dict)


_STANDARD = {"gpu_preference": "high", "game_mode": True}
_BALANCED = {**_STANDARD, "power_plan": "high", "priority": "above_normal"}
_MAX = {**_BALANCED, "priority": "high", "capture": "off", "background": {"action": "lower"}}

PROFILES = [
    BoostProfile("standard", msg("Standard"), msg("High-performance GPU for the game and Windows Game Mode."),
                 _STANDARD),
    BoostProfile("balanced", msg("Balanced"), msg("Standard, plus the High-performance power plan and above-normal "
                                                  "priority while the game runs."), _BALANCED),
    BoostProfile("max_fps", msg("Maximum FPS"), msg("Balanced, plus high priority, no background recording and the "
                                                    "background programs you chose at lower priority."), _MAX),
    BoostProfile("low_latency", msg("Low Latency"), msg("Maximum FPS with a frame limit 3 FPS below the refresh rate, "
                                                        "which keeps G-SYNC/FreeSync active and the render queue short."),
                 {**_MAX, "frame_limit": "refresh-3"}),
    BoostProfile("streaming", msg("Streaming"), msg("Balanced without touching OBS, Discord or other streaming tools; "
                                                    "background recording is left as it is."),
                 {**_BALANCED, "background": {"action": "lower", "keep_streaming": True}}),
    BoostProfile("battery", msg("Battery Saver"), msg("Power-saving GPU, the Power saver plan and a 60 FPS limit."),
                 {"gpu_preference": "power_saving", "power_plan": "saver", "frame_limit": 60}),
    BoostProfile("custom", msg("Custom"), msg("Your own selection of the settings below."), {}),
]
BY_ID = {p.id: p for p in PROFILES}
