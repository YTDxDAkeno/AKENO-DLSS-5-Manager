# AKENO Gaming Suite 3.1.0 – Release-Bericht

Stand 2026-10-26. **Status: bereit zur Veröffentlichung, wartet auf Freigabe.** Nichts veröffentlicht, kein Tag,
kein Deployment, kein Eintrag im Admin-Panel, keine Änderung an Lizenzserver, Konten, Lizenzen, Datenbanken oder
an der veröffentlichten 3.0.0.

## Release-Build

| | Wert |
|---|---|
| Release-Commit | `e2c65993fe0121f10a9bbc00b1187201b4b33d1c` (Branch `claude/adoring-shannon-2d4qb4`, PR #18) |
| CI-Lauf | „Build and test“ Run 203, ID `40349701652` (push), alle 5 ausgeführten Jobs grün |
| zweiter Lauf, gleicher Commit | Run 204, ID `40349709871` (pull_request), alle Jobs grün |
| Artefakt | `AKENO-Gaming-Suite-windows-x64`, ID `12665801210`, Digest `sha256:ffb134ec71cdde56fac0ea3dea6db426f25300efbe7fea1f3a3c87895d7844e7`, verfügbar bis 2027-01-24 |
| Veröffentlichungs-Jobs | „Publish to the public release repository“ und „Draft development release“: **übersprungen** (kein Tag, kein Dispatch) |

**Nur die Dateien aus Run 203 signieren.** Jeder neue CI-Lauf (auch der Lauf dieses Doku-Commits) baut Dateien mit
anderen Bytes. Das Quellcodearchiv gehört zu genau `e2c6599`.

## 1. Branch-Abhängigkeit

- Alle 23 Remote-Branches, auch `origin/main` und alle 3.0-Branches, sind Vorfahren des Release-Commits. Es fehlt
  keine 3.0- oder 3.1-Änderung.
- Kette: `main` (Initial-Commit) → gestapelte 3.0-PRs → `claude/blissful-wright-8zj111` @ `7b461d3` (3.0-Stand mit
  Updater-Schema 2) → PR #18 mit 13 Commits (`06fad15` … `e2c6599`).
- Die veröffentlichte 3.0.0 ist der Build von `e34a951` (CI-Run 187). Seitdem sind `akeno/updater/`,
  `akeno/licensing/` und `packaging/akeno.spec` unverändert. Download- und Prüfcode des installierten 3.0.0-Clients
  sind also derselbe Code, der hier getestet wurde.

## 2. Version

| Datei | Wert |
|---|---|
| `akeno/__init__.py` | `__version__ = "3.1.0"` |
| `packaging/installer.iss` | `AppVersion "3.1.0"` |
| EXE-Versionsressource | aus `__version__` (`packaging/akeno.spec`) |
| Test | `tests/test_migration_v3.py` prüft `3.1.0` |

`akeno/data/stack/components.json` „version“ ist die Katalogversion, nicht die Programmversion, und bleibt.

## 3. Release Notes

- `docs/RELEASE_NOTES.md`: neuer Abschnitt 3.1.0 (System Doctor, Smart Cleaner, Network Lab, Universal Mod Scanner,
  Security Center, Download-Schutz, Wiederherstellung ohne Lizenz, Berichtsexport, Fehlerbehebung Hardware-Gesundheit,
  Update-Hinweise). Der 3.0.0-Abschnitt bleibt darunter.
- `docs/release/3.1.0/release-notes.txt`: Text für das Feld `release_notes` im Release Manager (Deutsch + Englisch,
  3 246 Zeichen, Grenze 8 000).

## 4. Leere Hardware-Gesundheitstabelle – Ursache und Behebung

**Ursache (nachgewiesen):** `powershell_json` hängte `| ConvertTo-Json …` in eine neue Zeile an das Skript. Windows
PowerShell 5.1 lehnt eine Zeile ab, die mit `|` beginnt („An empty pipe element is not allowed“). Jede mehrzeilige
Abfrage lieferte deshalb nichts: Laufwerke/SMART (`Get-PhysicalDisk`, `Get-StorageReliabilityCounter`,
`Win32_DiskDrive`), Leistungsindikatoren und Netzwerkdetails. Die Fehlermeldung kam als CLIXML und war unlesbar.
Die Stand-in-Tests konnten das nicht sehen, weil sie kein echtes PowerShell aufrufen.

**Nachweis:** Neue Windows-Tests (`tests/test_windows_queries.py`, Commit `f2978a3`) mit den echten Abfragen
schlugen in CI-Run 197 fehl (4 Fehler, Meldung „#< CLIXML …“).

**Behebung (Commit `c0f8f7f`):**
- Pipe direkt hinter der letzten Anweisung (`script.rstrip() + " | ConvertTo-Json -Depth 5 -Compress"`).
- CLIXML-Fehlerausgabe wird lesbar dekodiert (`clixml_text`, `first_line`).
- Fallback: Laufwerke, die nur `Win32_DiskDrive` meldet, erscheinen mit SMART-Status („Nur der SMART-Status ist
  verfügbar.“); „Pred Fail“ ergibt einen PROBLEM-Befund.
- Es werden keine Werte erfunden: was Windows nicht meldet, bleibt leer mit Hinweis.

**Ergebnis auf dem echten Windows-Runner (Run 203):** Die vier Windows-Tests sind grün. Der System Doctor liest das
Laufwerk mit Temperatur aus: `doctor.hardware (7.9 s): [warn] Microsoft NVMe Direct Disk v2 is hot` (Azure-NVMe des
Runners). Die Tabelle ist also nicht mehr leer.

## 5. Tests und Windows-Buildprüfungen (Run 203)

| Prüfung | Ergebnis |
|---|---|
| Linux (offscreen Qt) | **1204 bestanden, 17 übersprungen** (850 s) |
| lokal (Linux) | 1203 bestanden, 17 übersprungen (die 4 Windows-Abfragetests werden dort übersprungen) |
| Windows: Unit-, Integrations- und UI-Tests | grün |
| Diagnose-UI bei 150 %, Smoke-Test aus dem Quellcode, alle Seiten gebaut | grün |
| 3.1-Systemwerkzeuge gegen den echten Runner (nur lesend) | grün, siehe unten |
| SYSTEM-Seiten mit Testdaten, Screenshots, FiveM-Fixture-Screenshots | grün |
| ReShade (64/32 Bit) aus geprüftem Quellcode, Community-Abhängigkeiten, Vertragstests | grün |
| PyInstaller, gepackter Build vertraut API, Lizenz- und Release-Schlüssel | grün |
| Smoke-Test der gepackten EXE, GUI startet und läuft, Desktop Studio | grün |
| Audit: keine proprietären NVIDIA-Laufzeiten im öffentlichen Paket | grün |
| Portable-ZIP, Installer (Inno Setup), Release-Manager-Werte, Quellcodearchiv, Prüfsummen | grün |
| Originale Upstream-Pakete (Download + Produktions-Extraktor) | grün |
| Lizenzserver-Tests (SQLite, PostgreSQL 16, Docker-Deployment **nur im CI-Container**) | grün |
| Windows-Update E2E inkl. Upgrade 3.0.0 → 3.1.0 | grün, siehe 9 |

**Echte Windows-Sicherheitsprüfung auf dem Runner:**
- EICAR: `verdict=threats threats=['Virus:DOS/EICAR_Test_File']`, Download-Prüfung `threats` → der Download-Schutz
  stoppt die Installation. Microsoft Defender wird dabei nur abgefragt, nichts deaktiviert, nichts ausgenommen.
- Datei-Prüfung: `sample-mod.jar av=clean`, `python.exe av=clean`.
- Firewall: alle drei Profile an. Echtzeitschutz und Tamper Protection sind auf dem CI-Image aus und werden korrekt
  als `[fail]` bzw. `[warn]` gemeldet (Runner-Einstellung, nicht von AKENO verändert).
- Windows-Sicherheitscenter (`wscapi`) gibt es auf Windows Server nicht; das wird als Fehlertext angezeigt, nicht
  als Urteil.

## 6. Release-Dateien (Run 203)

| Datei | SHA-256 | Byte |
|---|---|---|
| `AKENO-Gaming-Suite-3.1.0-Setup-x64.exe` | `56d33da6c8ad35ed2afffdbb820a9f882231455ef93c759ab02afc04e92b8ca0` | 157 960 587 |
| `AKENO-Gaming-Suite-3.1.0-portable-x64.zip` | `ed51a967f42e221371793ff62139f32f72e6957adb1daf05f2355391aee52640` | 223 583 996 |
| `AKENO-Gaming-Suite-3.1.0-source.zip` | `ceafdbcf58f5a18bb8d74395f3d317b8aed1fbbcb181b2a20c3d606fc17e59fd` | |
| `AKENO-community-dependencies-3.1.0-x64.zip` | `cf31cf0cdcfeb6e581f2a2ff1b332c69cc13cc17b87679b5dfd8c74a97dd13fc` | |

Dazu `SHA256SUMS.txt` mit genau diesen vier Zeilen.

**Werte für den Release Manager** (`tools/release_v2.py describe`, mit den Entpackregeln des Updaters geprüft):

| Feld | Wert |
|---|---|
| portable `size_bytes` | 223583996 |
| portable `sha256` | `ed51a967f42e221371793ff62139f32f72e6957adb1daf05f2355391aee52640` |
| portable `file_count` | 2907 |
| portable `uncompressed_size_bytes` | 493999889 |
| portable `pe_files` | 356 |
| portable `content_sha256` | `beec2fc7da449f4231e4ccf51af1f225bb89970ad53cc897122ed99d8a20755d` |
| Ordnerebene im ZIP | keine (Dateien direkt oben) |
| vom Updater akzeptiert | ja |
| installer `size_bytes` | 157960587 |
| installer `sha256` | `56d33da6c8ad35ed2afffdbb820a9f882231455ef93c759ab02afc04e92b8ca0` |
| innerhalb der Release-Manager-Grenzen | ja |

## 7. Produktions-Release-Infrastruktur (nur gelesen)

- Der Auto-Updater nutzt **Release-Schema 2** unter `https://api.akeno.online/api/v1/releases/*`: Ed25519-Signatur
  über `signed_payload`, Release-Schlüssel `d6139235b220a4aa`, Anti-Replay über `sequence`, `expires_at` höchstens
  30 Tage, Download-Adressen genau `https://akeno.online/downloads/<version>/AKENO-Setup.exe` und
  `…/AKENO-Portable.zip`, In-App-Update nur mit `install_mode = portable-directory-swap`.
- Die älteren GitHub-Kanalmanifeste (Schema 1, Job „Publish to the public release repository“) sind **nicht** der
  Weg; deshalb kein Tag `v3.1.0`.
- Aktuell veröffentlicht (Produktions-Lesecheck, Run 20): Stable **3.0.0**, Sequenz 1, ausgestellt
  2026-10-26T02:26:15Z, gültig bis **2026-11-25T02:26:15Z**, `portable-directory-swap`, Installer
  `fe919b72…0dd20f` (157 148 888 Byte), Portable `d4c57019…95e9f9` (222 769 069 Byte).

## 8. Status Release Manager

| Punkt | Status |
|---|---|
| 3.0.0-Release | veröffentlicht, bleibt unverändert |
| 3.1.0-Release | **noch nicht angelegt** |
| Signatur | **offen** – Signaturanfrage im Admin-Panel, offline mit dem bestehenden Schlüssel `d6139235b220a4aa` |
| neue Schlüssel | keine erzeugt; kein privater Schlüssel in Repository, CI oder Logs |
| Bedingungen für 3.1.0 | Sequenz > 1, `portable-directory-swap` mit Portable-Paket, `minimum_supported_version` leer oder ≤ 3.0.0 |
| Prüfwerkzeug | `tools/release_v2.py verify … --installed 3.0.0 --previous-sequence 1` vor der Freischaltung |

Die einzelnen Schritte stehen in `docs/release/3.1.0/VEROEFFENTLICHUNG.md`.

## 9. Upgradepfad 3.0.0 → 3.1.0 (echter 3.0.0-Installer)

CI lädt den veröffentlichten Installer `https://akeno.online/downloads/3.0.0/AKENO-Setup.exe`, prüft ihn gegen den
signierten SHA-256 `fe919b72…`, installiert ihn in ein frisches Benutzerprofil mit Lizenzstatus (DPAPI),
Einstellungen, Themes, Wallpapern, Mod-Backups und Benutzerdateien und aktualisiert dann auf den 3.1.0-Build dieses
Laufs. **57 bestanden, 0 fehlgeschlagen.**

- installierte EXE ist 3.0.0; nach Installation: Daten, Lizenz, Einstellungen unverändert
- Update 3.0.0 → 3.1.0 installiert und Startprüfung bestanden; Journal
  `prepared → stopping → copying → swapping → swapped → health → committing → committed`
- Programmordner Datei für Datei = geprüftes 3.1.0-Paket; EXE meldet 3.1.0; 3.1.0 läuft; 3.0.0 für Rollback
  aufbewahrt; Windows zeigt 3.1.0 unter „Installierte Apps“; Deinstallationsprogramm erhalten
- nach dem Update: Themes, Wallpaper, Mod-Backups, Benutzerdateien (SHA-256), Lizenz (DPAPI) und Einstellungen
  unverändert
- **automatischer Rollback**: Build ohne Fenster/Bericht, Build mit fehlgeschlagenen Eigenprüfungen und Build mit
  falscher Version werden zurückgerollt; danach wieder genau 3.1.0, Daten/Lizenz/Einstellungen unverändert
- Stromausfall direkt nach dem Umschalten: einmalige Wiederherstellung über RunOnce stellt die vorige Version her
- manipuliertes Paket, fremder Signaturschlüssel, falsche `content_sha256`, falsche Dateizahl, falsche entpackte
  Größe: abgelehnt, nichts bleibt im Staging, Programmordner unverändert
- **manueller Rollback auf 3.0.0** bestanden, Daten/Lizenz/Einstellungen unverändert
- Deinstallation entfernt das Programm, Benutzerdaten und Lizenz bleiben

Hinweis zur Testumgebung: Für die Testinstallation wird der Release-Schlüssel durch einen Wegwerf-Testschlüssel
ersetzt (`737ef73b5f8f093d`), damit CI eigene Releases signieren kann. Das Testpaket wird aus dem 3.1.0-Build
dieses Laufs neu gepackt und enthält eine zusätzliche Testdatei mit Unicode-Namen (daher 2 908 statt 2 907
Dateien). Der echte Schlüssel und die echte Signatur kommen erst beim
Signieren im Admin-Panel ins Spiel; das prüft Schritt 6 von `VEROEFFENTLICHUNG.md` mit `release_v2.py verify`.

Zusätzlich in Run 203: das reguläre Update-E2E (3.1.0 → 3.1.1 usw.) mit Installer, Updater, Startprüfung, Rollback
und Wiederherstellung – grün.

## 10. Offene Punkte

- **Kein Authenticode-Codesigning**: Setup und EXE sind nicht signiert (wie 3.0.0). SmartScreen kann warnen. Die
  Update-Kette ist über Ed25519 und SHA-256 abgesichert.
- **Gültigkeit**: Signierte Release-Informationen gelten höchstens 30 Tage. Rechtzeitig neu ausstellen (neue Sequenz).
- **Zurücknehmen nach Freischaltung**: Clients, die 3.1.0 gesehen haben, nehmen keine kleinere Version mehr an.
  Korrektur nur über 3.1.1; auf einem PC selbst über KONTO → Updates → „Zurück zu 3.0.0“.
- Echte Hardware: SMART/NVMe-Werte auf deinem PC nach dem Update noch einmal im System Doctor ansehen (der Runner hat
  nur eine virtuelle NVMe).
- Bekannte Grenzen aus `docs/handover/AKENO_3_1_HANDOVER.md` (kein Virenschutz-Urteil bei fremdem AV mit
  ausgeschaltetem Defender, Netzwerkverkehr pro Programm nur als Verbindungen, lokale Importe ohne Download-Schutz).
