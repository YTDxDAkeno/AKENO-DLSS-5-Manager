# AKENO 3.1 – Übergabe / Handover

Stand 2026-10-26, Branch `claude/adoring-shannon-2d4qb4`. Nichts veröffentlicht, nichts gemergt, kein Release,
kein VPS-Deployment, keine Änderung an Lizenzsystem, Schlüsseln oder Benutzerkonten.

## Basis

- Branch-Basis: `claude/blissful-wright-8zj111` @ `7b461d3` (Updater Schema 2, CI-Run 188 grün). `main` enthält
  nur den Initial-Commit; alle PRs sind gestapelte Draft-PRs, der 3.1-PR zielt auf `claude/blissful-wright-8zj111`.
- Baseline-Tests (Linux, offscreen): 1039 bestanden, 13 übersprungen.

## Commits

| Commit | Inhalt |
|---|---|
| `06fad15` | gemeinsamer Kern `akeno/system/` + System Doctor |
| `2d3edc2` | CI: `tools/system_check.py` gegen den echten Windows-Runner; Handover |
| `5154ae4` | Smart Cleaner |
| `13c2f5b` | Network Lab |
| `99765a8` | Universal Mod Scanner + neue Mod-Bibliothek (CI-Run 192 komplett grün inkl. Windows) |
| `2c4fff1` | Security Center + Download-Schutz |
| `b12f7fa` | Wiederherstellung ohne Lizenz, Berichtsexport, Testdaten-Screenshots, Doku (CI-Runs 193/194 komplett grün) |

Draft-PR: YTDxDAkeno/AKENO-DLSS-5-Manager#18 (Basis `claude/blissful-wright-8zj111`).

## Status

| Modul | Status |
|---|---|
| Gemeinsame Infrastruktur | fertig, getestet |
| System Doctor | fertig, getestet (Stand-ins + echter Runner) |
| Smart Cleaner | fertig, getestet |
| Network Lab | fertig, getestet; VPN bewusst nur Erkennung (siehe `docs/AKENO_3_1_SYSTEM_TOOLS.md`) |
| Universal Mod Scanner | fertig, getestet |
| Security Center | fertig, getestet mit `FakeSecurity`; echter Defender/WSC über `system_check.py --eicar` in CI |
| Download-Schutz | `DownloadManager.antivirus` nach SHA-256; Fund stoppt Installation; Tests mit Stand-in |
| Wiederherstellung ohne Lizenz | Willkommensbildschirm „Spieldateien wiederherstellen…“, `akeno/recovery.py` |
| Deutsch | vollständig (Coverage-Test), durchgehend „du“ wie der Rest der App |

Technische Beschreibung: `docs/AKENO_3_1_SYSTEM_TOOLS.md`.

## Noch anzusehen

1. Run 193, Windows-Job, Step „AKENO 3.1 system tools against the real runner“: Der Schritt ist grün (kein
   Python-Fehler mit den echten Windows-Providern). Die Einzelwerte für `Security` und `EICAR` stehen im Step
   Summary bzw. im Artefakt `windows-test-results-and-screenshots` (`system-check.json`). Aus der
   Entwicklungsumgebung waren sie nicht abrufbar (Log-Speicher gesperrt). Erwartung: EICAR-Urteil `threats`, wenn
   Defender auf dem Runner aktiv ist, sonst `not_checked` mit Grund.
2. Artefakt `screenshots/system-fixtures/*.png` (Testdaten mit rotem Banner).

## Offene Punkte / bekannte Grenzen

- Kein Virenschutz-Urteil, wenn ein fremdes Produkt aktiv und Defender aus ist (wird als „nicht geprüft“ gezeigt).
- Pro-Programm-Netzwerkverkehr nur als Verbindungen + Gesamt-I/O (ohne ETW/Admin nicht genauer).
- Der signierte AKENO-Updater läuft bewusst nicht durch den Download-Virenscan (eigene Ed25519-Kette).
- Lokale Komponenten-Importe (`import_file`) sind nicht an den Download-Schutz angeschlossen.

## Nächster Schritt

Nutzer-Testplan auf echter Hardware (PR #18, Abschnitt H), danach Review des Draft-PRs. Kein Release ohne
ausdrückliche Freigabe.
