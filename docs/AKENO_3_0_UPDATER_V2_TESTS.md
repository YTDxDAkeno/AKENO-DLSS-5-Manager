# AKENO 3.0.0 – Testergebnisse Updater (Release-Schema 2)

Stand 2026-10-25. Nur tatsächlich ausgeführte Tests; alles andere steht unter „Ungetestet“.

## 1. Automatisiert getestet

| Ebene | Wo | Ergebnis |
|---|---|---|
| Unit-, Integrations- und UI-Tests, Linux (offscreen Qt) | lokal und CI „Tests (Linux)“ | gesamte Suite grün: lokal 869 bestanden, 6 übersprungen (vorher 812/6, keine Regression) |
| dieselben Tests auf Windows Server 2022 | CI „Windows build, tests and smoke test“ | grün (Läufe 176, 177, 179, 180) |
| neue Updater-Tests `tests/test_updater_v2.py` | Linux und Windows | 58 Tests (1 nur Windows: echte NTFS-Junction) |
| bestehende Updater-Tests (`test_updater*.py`) | Linux und Windows | grün, an Health-Bericht angepasst |
| Lizenz-/Kontotests, Lizenzserver (SQLite, PostgreSQL 16, Docker) | CI | grün, Lizenzcode unverändert |
| Echte HTTPS-Verbindung (eigene Test-CA), Abruf v2-Metadaten, Download, Umleitung zu fremdem Host abgelehnt, 404 = aktuell, 503 = nicht erreichbar | `test_release_information_and_package_over_real_https` | grün |
| Live-Schema der Produktion gelesen (read-only) | CI „Production read-only check“ | grün; Signierschlüssel `d6139235b220a4aa` im Build; noch kein Release veröffentlicht |

Abgedeckte Fälle (Auszug): gültige/ungültige/fremde Signatur, Lizenzschlüssel als Release-Schlüssel, außen ≠ innen,
fehlende signierte Felder (alle 14), doppelte JSON-Schlüssel, NaN, Produkt/Plattform/Kanal/Version/Sequenz/Modus
falsch, fremde/HTTP-/Query-Download-Adressen, abgelaufen, Uhr zurückgestellt, Sequenz-Replay, gleiche Sequenz mit
anderem Inhalt, Downgrade, inkompatibles Protokoll, `manual-installer`, ungültige Authenticode-Signatur,
`minimum_supported_version`, 15 unsichere ZIP-Namen, Symlinks, Sonderdateien, verschlüsselte Einträge, Duplikate,
Datei/Ordner-Konflikte, ZIP-Bombe, falsch deklarierte Größen, beschädigtes Archiv, nachträglich veränderte
Staging-Dateien, parallele Updates, kein Speicherplatz, Health-Check (eigene Prüfungen fehlgeschlagen, falsche
Version, Absturz direkt nach dem Bericht, Absturz vor dem Bericht), Stromausfall nach dem Tausch, Wiederaufnahme
jeder Phase (idempotent), Übergabe beim nächsten Start, Benutzerdaten unverändert, Deinstaller erhalten,
Updater-Start nicht bestätigt → AKENO bleibt offen, UI (Anzeige, Hintergrund-Download, Abbrechen, Installation erst
nach Bestätigung, Optionen), Maintainer-Werkzeug `release_v2.py`.

## 2. Auf Windows praktisch getestet (echte Installation, echte EXE-Dateien)

CI-Job „Windows update end to end“ (`tools/update_e2e.py`) auf einem Windows-Server-2022-Runner. Installiert wird
die in derselben CI-Pipeline gebaute `AKENO-Gaming-Suite-3.0.0-Setup-x64.exe`; das Update ist ein Build desselben
Commits mit Version 3.0.1. Der lokale Release Manager läuft als `https://akeno.online` auf dem Runner (hosts-Datei,
Test-CA), der Release-Prüfschlüssel ist **nur in dieser Testinstallation** durch einen Wegwerf-Testschlüssel ersetzt.

Ergebnis Lauf 180: **49 von 50 bestanden**. Der eine Fehler war ein Fehler im Testskript (die „Manipulation“ des
Downloads veränderte kein Byte); behoben in `9137296`. Der Abschnitt wird mit dem nächsten Lauf aktualisiert.

| Szenario | Ergebnis (Lauf 180) |
|---|---|
| Installer still, pro Benutzer; Deinstaller im Programmordner; installierte EXE = 3.0.0 | OK |
| Update 3.0.0 → 3.0.1 mit zweitem offenen AKENO-Fenster; Health-Check bestanden; Journal vollständig | OK |
| Programmordner = geprüftes Paket, 2891 Dateien per SHA-256 | OK |
| neue EXE meldet 3.0.1, läuft nach dem Update; 3.0.0 als Rollback aufbewahrt | OK |
| Deinstaller erhalten; „Installierte Apps“ zeigt 3.0.1; kein RunOnce-Rest | OK |
| kaputtes Update (Qt fehlt, kein Fenster) → automatisch zurück; 3.0.1 läuft wieder, Datei für Datei identisch | OK |
| Update mit fehlenden Programmdaten → eigene Prüfungen schlagen fehl → zurück | OK |
| falscher Build (meldet andere Version) → zurück | OK |
| Stromausfall direkt nach dem Ordnertausch: RunOnce gesetzt; nächster AKENO-Start übergibt an den Updater, vorherige Version wiederhergestellt; RunOnce entfernt | OK |
| manipulierter Download | Testskriptfehler (s. o.) |
| Release mit fremdem Schlüssel abgelehnt; Programmordner unverändert | OK |
| manueller Rollback 3.0.1 → 3.0.0 mit Health-Check | OK |
| Deinstallation entfernt das Programm | OK |
| nach jedem Schritt: Lizenzzustand (DPAPI) lesbar und unverändert, Einstellungen, Themes, Wallpaper unverändert (auch nach der Deinstallation) | OK |

## 3. Ungetestet

* **Windows 11 auf echter Hardware** (getestet: Windows Server 2022 im CI). Ebenso kein Test mit aktivem
  Drittanbieter-Virenscanner, mit einem echten Stromausfall (simuliert: Prozessabbruch nach dem Tausch) oder einem
  echten Neustart mit RunOnce bei der Anmeldung (geprüft: Eintrag wird gesetzt und entfernt; ausgeführt wurde die
  Wiederherstellung über den nächsten AKENO-Start).
* **Klick auf „Jetzt installieren“ in der echten Oberfläche**: der Updater wurde im E2E-Test so gestartet, wie die
  Seite ihn startet; die Seite selbst ist per UI-Test geprüft.
* **Gegen die Produktion**: kein v2-Release veröffentlicht, daher keine Prüfung eines echten, mit dem produktiven
  Schlüssel signierten Releases (offene Punkte in `AKENO_3_0_UPDATER_V2.md` §11).
* **Lizenzaktivierung mit echtem Konto** nach einem Update (geprüft: Lizenzzustand bleibt entschlüsselbar und
  unverändert, die neue Version kann ihn lesen; eine Anmeldung am Produktionsserver wurde nicht durchgeführt).
* **Authenticode-signierte Builds** (die CI-Builds sind nicht code-signiert; die Regel „gleicher Signierer“ ist per
  Unit-Test geprüft).
* `content_sha256` (Algorithmus nicht dokumentiert, wird nicht geprüft).
