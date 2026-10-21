# Real launcher renders — synthetic data

These PNGs are screenshots of the integrated AKENO 1.2 PySide6 application, not UI mockups. FiveM files, GPU/driver description and server policy are **TEST FIXTURES**, not a real RTX 4080 or real server. The screenshots deliberately show missing verification/permission and disabled installation rather than invented success.

Generate with `python tools/fivem_fixture.py HOME GAMES`, then `AKENO_HOME=HOME QT_QPA_PLATFORM=offscreen python -m akeno --screenshot OUTPUT`. The command captures all eight pages and all four FiveM tabs. Windows Actions additionally renders the fixture at 150% and uploads its screenshots. No fixture DLL/add-on is executed.

Refreshed from application commit `210d6fb` (English normal scale, German 150%). No runtime or FiveM server was executed.

`windows-evidence-en.png` and `windows-server-policy-150pct.png` are downloaded Windows Actions renders from commit `64cda9d`, run 39524328413, after fixing offscreen font discovery. Both were visually inspected; their GPU/server data remains synthetic.
