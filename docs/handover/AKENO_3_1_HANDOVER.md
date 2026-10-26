# AKENO 3.1 – Übergabe / Handover

Stand 2026-10-26, Branch `claude/adoring-shannon-2d4qb4`. Nichts veröffentlicht, nichts gemergt, kein Release,
kein VPS-Deployment, keine Änderung an Lizenzsystem, Schlüsseln oder Benutzerkonten.

**3.1.0 Stable ist gebaut und geprüft und wartet auf Freigabe:** Release-Commit `e2c6599`, CI-Run 203 komplett grün
(inkl. Upgrade vom echten 3.0.0-Installer, 57/57). Bericht: `docs/release/3.1.0/RELEASE_BERICHT.md`, Schritte:
`docs/release/3.1.0/VEROEFFENTLICHUNG.md`.

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
| `4933118` | Handover |
| `f2978a3` | Windows-Tests mit den echten PowerShell-Abfragen (Run 197 rot: Fehler nachgewiesen) |
| `c0f8f7f` | Fix leere Hardware-Gesundheitstabelle (PowerShell 5.1 lehnte mehrzeilige Abfragen ab), SMART-Fallback |
| `ef955fe` | Release 3.1.0: Version, Release Notes, Quellcodearchiv, Upgrade-Test vom veröffentlichten 3.0.0 |
| `ddcd52c`, `e2c6599` | CI: Timeout Konto-Fixture, Release-Zusammenfassung, frisches Profil für den Upgrade-Test (Runs 203/204 grün) |

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

## Echte Windows-Werte (Run 203, Schritt „Release summary“)

- EICAR: `verdict=threats`, `Virus:DOS/EICAR_Test_File`, Download-Prüfung `threats`.
- System Doctor Hardware: `[warn] Microsoft NVMe Direct Disk v2 is hot` (Laufwerk und Temperatur gelesen).
- Artefakt `screenshots/system-fixtures/*.png` (Testdaten mit rotem Banner) in `windows-test-results-and-screenshots`.

## Offene Punkte / bekannte Grenzen

- Kein Virenschutz-Urteil, wenn ein fremdes Produkt aktiv und Defender aus ist (wird als „nicht geprüft“ gezeigt).
- Pro-Programm-Netzwerkverkehr nur als Verbindungen + Gesamt-I/O (ohne ETW/Admin nicht genauer).
- Der signierte AKENO-Updater läuft bewusst nicht durch den Download-Virenscan (eigene Ed25519-Kette).
- Lokale Komponenten-Importe (`import_file`) sind nicht an den Download-Schutz angeschlossen.

## Nächster Schritt

Freigabe für 3.1.0 Stable einholen, dann `docs/release/3.1.0/VEROEFFENTLICHUNG.md` Schritt für Schritt (Admin-Panel,
Offline-Signatur, `release_v2.py verify`, Veröffentlichen, Produktions-Lesecheck). Kein Release ohne ausdrückliche
Freigabe; kein Tag `v3.1.0` (alter Schema-1-Kanal).
