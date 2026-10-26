# AKENO 3.1 – Übergabe / Handover

Stand: laufend aktualisiert während der Entwicklung (Branch `claude/adoring-shannon-2d4qb4`).

## Basis

- Branch-Basis: `claude/blissful-wright-8zj111` @ `7b461d3` (Updater Schema 2, CI-Run 188 grün). Alle
  früheren Branches (v1.2 … Desktop Studio 2.0, Accounts, 3.0) sind lineare Vorfahren dieser Spitze.
- `main` enthält nur den Initial-Commit; alle PRs (#1 … #17) sind gestapelte Draft-PRs.
- Baseline-Tests (Linux, offscreen): 1039 bestanden, 13 übersprungen.

## Architektur 3.1 (neu)

```
akeno/system/            Kern ohne Qt
  model.py               Finding / Evidence (gemessen | mögliche Ursache | Korrelation) / Reading / Report
  tasks.py               TaskContext: Abbruch, Zeitlimit, Fortschritt
  commands.py            Windows-Werkzeuge ohne Shell, PowerShell kodiert, UAC nur nach Bestätigung
  store.py               system.sqlite3 (Journal, Historie, Zustand) – getrennt von akeno.sqlite3
  fs.py                  sicheres Durchlaufen (keine Links/Junctions), Containment, Identität, Papierkorb
  signatures.py          Authenticode eingebettet + Katalog (Get-AuthenticodeSignature)
  lnk.py                 .lnk-Parser (nur lesen)
  suite.py               SystemTools an AppServices.system_tools (lazy)
  doctor/                System Doctor
akeno/ui/system_ui.py    gemeinsame UI-Bausteine (TabbedPage, TaskBar, FindingView, ReadingTile, Sparkline)
akeno/ui/pages/doctor.py System Doctor Seite
tools/system_check.py    echte Provider auf dem Windows-CI-Runner (nur lesend)
```

## Status

| Modul | Status |
|---|---|
| Gemeinsame Infrastruktur | fertig, getestet |
| System Doctor | fertig (Dashboard, Slow PC, Startup, Crashes, Hardware, Repair), getestet mit Stand-in-Providern |
| Smart Cleaner | in Arbeit |
| Network Lab | offen |
| Universal Mod Scanner | offen |
| Security Center | offen |

## Nächster Schritt

Siehe Abschnitt „Status“; die Aufgabenliste im PR-Bericht ist maßgeblich.
