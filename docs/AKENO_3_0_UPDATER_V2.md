# AKENO 3.0.0 – Release API v2, Windows-Updater, Rollback

Stand: 2026-10-25, Branch `claude/blissful-wright-8zj111` (aufgesetzt auf `claude/magical-franklin-b8gulz`, den
neuesten Stand mit allen 3.0-Arbeiten). Nichts gemergt, nichts veröffentlicht, auf dem VPS nichts verändert.

Ergebnisse der Tests stehen in [AKENO_3_0_UPDATER_V2_TESTS.md](AKENO_3_0_UPDATER_V2_TESTS.md).

## 1. Ausgangslage und Quellen

| Quelle | Stand |
|---|---|
| `CODEX_CLAUDE_RELEASE_V2.md`, `BETREIBER_ANLEITUNG.md`, `ABSCHLUSSBERICHT.md` | **in keinem Branch dieses Repositorys vorhanden**, auch nicht in anderen Repositorys des Kontos |
| API-v2-Backend-Code | nicht im Repository (der Produktionsserver weicht bewusst vom Server in `server/` ab) |
| Direkter Zugriff auf `akeno.online` aus dieser Arbeitsumgebung | durch die Netzwerkrichtlinie gesperrt (HTTP 403 am Proxy) |
| **Öffentliches OpenAPI-Dokument der Produktion** | gelesen über den bestehenden Read-only-Produktionscheck in GitHub Actions (`production-check.yml`, `tools/api_contract.py --schemas`), 2026-10-25 15:42 UTC |

Deshalb gilt: Alles, was der Client prüft, stammt aus dem live veröffentlichten Schema. Wo das Schema schweigt
(z. B. welche Bytes genau signiert sind, wie `content_sha256` gebildet wird), lehnt der Client lieber ab oder prüft
nicht, statt etwas zu erfinden. Diese Punkte stehen in Abschnitt 11.

### Was die Produktion veröffentlicht (gelesen, nicht vermutet)

* **„API v2“ ist das Release-Schema 2 unter `/api/v1/releases/*`.** `/api/v2/openapi.json` und `/api/v2/system/version`
  liefern 404.
* `GET /api/v1/releases/stable` → `ReleaseEnvelope` (heute 404: noch kein Release veröffentlicht),
  `GET /releases` → `{"releases": []}`, `GET /releases/signing-key` → `d6139235b220a4aa`
  (`bpe9acmephsoZSYzy12R2XFy_lJZ_69MiV31ekehcdo`), unverändert und im Build eingebaut.
* `ReleaseEnvelope` (Pflichtfelder): `schema_version` (= 2), `updater_protocol` (= 2), `product`
  (`akeno-gaming-suite`), `channel` (`stable`), `version` (X.Y.Z), `release_notes` (1–8000), `release_date`,
  `published_at`, `issued_at`, `expires_at`, `sequence` (1 … 2^53−1), `request_id` (32 hex), `platform`
  (`windows-x64`), `download_url`, `size_bytes` (≤ 512 MiB), `sha256`, `authenticode`, `minimum_supported_version`
  (oder null), `unattended_install_allowed` (bool), `install_mode` (`manual-installer` | `portable-directory-swap`),
  `signed_payload` (string), `signature` (`{algorithm: Ed25519, key_id: 16 hex, value}`); optional `portable`.
* `PortablePackage`: `download_url`, `size_bytes` (≤ 512 MiB), `sha256`, `authenticode`, `pe_files` (1–512),
  `verified_pe_files` (0–512), `file_count` (1–4096), `uncompressed_size_bytes` (≤ 2 GiB), `content_sha256`.
* `Authenticode`: `verified` (bool), `status` (`verified`, `not_signed`, `present_unverified`, `invalid`, `mixed`),
  `publisher_certificate_sha256` (oder null).

Beispiel einer Antwort in genau dieser Form (synthetisch, ohne echte Werte, so wie sie die Tests verwenden):

```json
{
  "schema_version": 2, "updater_protocol": 2, "product": "akeno-gaming-suite", "channel": "stable",
  "platform": "windows-x64", "version": "3.0.1", "sequence": 7, "request_id": "0123456789abcdef0123456789abcdef",
  "issued_at": "2026-11-02T09:00:00Z", "expires_at": "2026-12-02T10:00:00Z",
  "release_notes": "…", "release_date": "2026-11-02", "published_at": "2026-11-02T10:00:00Z",
  "install_mode": "portable-directory-swap", "unattended_install_allowed": false, "minimum_supported_version": null,
  "download_url": "https://akeno.online/downloads/AKENO-Gaming-Suite-3.0.1-Setup-x64.exe",
  "size_bytes": 123456789, "sha256": "…64 hex…",
  "authenticode": {"verified": false, "status": "not_signed", "publisher_certificate_sha256": null},
  "portable": {"download_url": "https://akeno.online/downloads/AKENO-Gaming-Suite-3.0.1-portable-x64.zip",
               "size_bytes": 260000000, "sha256": "…", "content_sha256": "…", "file_count": 1500,
               "uncompressed_size_bytes": 700000000, "pe_files": 300, "verified_pe_files": 0,
               "authenticode": {"verified": false, "status": "not_signed", "publisher_certificate_sha256": null}},
  "signed_payload": "{…dieselben Felder als JSON-Text…}",
  "signature": {"algorithm": "Ed25519", "key_id": "d6139235b220a4aa", "value": "…base64url…"}
}
```

## 2. Bestandsaufnahme (vor der Änderung)

| Bereich | Befund |
|---|---|
| Technik | Python 3.12, PySide6 6.8.1, cryptography 44, psutil; PyInstaller-One-Folder-Build, Inno-Setup-Installer (pro Benutzer, ohne Admin) |
| Version | `akeno/__init__.py`: 3.0.0 |
| Update-Client | `akeno/updater/` verstand nur Release-Schema **1** → hätte jedes v2-Release als „unbekanntes Format“ abgelehnt. Schema-1-Releases wurden nur angezeigt, nie installiert (kein portables Paket) |
| Updater/Rollback | separater Prozess mit Ordnertausch, Marker „Fenster offen“ nach 3 s, Rollback bei Fehlstart; **kein Journal**, keine Wiederaufnahme nach Stromausfall, kein Beenden weiterer AKENO-Prozesse (Desktop-Studio-Runtime aus dem Programmordner hätte den Tausch blockiert), „läuft“ galt als „gesund“ |
| Benutzerdaten | alles in `%LOCALAPPDATA%\AKENO` (Einstellungen-DB, Lizenz `license\state.bin` mit DPAPI, Spiele, Backups, Desktop Studio, Themes, Wallpaper, Sync); **nichts im Programmordner** außer einem optionalen portablen Datenordner (`AKENO_HOME` im Programmordner) und dem Deinstaller von Inno Setup |
| Lizenz | Gerätebindung über `MachineGuid`, signierte Leases, offline-fähig; unabhängig vom Programmordner |
| Downloads | `akeno/security/downloads.py`: HTTPS, Host-Allowlist inkl. Umleitungen, Fortsetzen (Range), SHA-256 beim Streamen, 256-MiB-Grenze |
| CI | Linux-Tests, Server-Tests, Windows-Build mit Smoke-Tests, Installer, portables ZIP, Checksummen; Read-only-Produktionscheck |
| Artefakte | `AKENO-Gaming-Suite-<v>-Setup-x64.exe`, `AKENO-Gaming-Suite-<v>-portable-x64.zip`, `SHA256SUMS.txt` (CI-Artefakt `AKENO-Gaming-Suite-windows-x64`) |

## 3. Wie die Ed25519-Verifikation funktioniert (`akeno/updater/api_v2.py`)

1. Antwort nur über HTTPS von `api.akeno.online`, Umleitungen nur zu Hosts des Builds (sonst Abbruch), max. 1 MiB.
2. `signature.algorithm` muss `Ed25519` sein, `signature.key_id` ein **im Programm eingebauter Release-Schlüssel**
   (`akeno/data/release-keys.json`, heute nur `d6139235b220a4aa`). Lizenzschlüssel zählen nie.
3. Die Signatur wird über **genau die UTF-8-Bytes von `signed_payload`** geprüft, unverändert. Es wird nichts neu
   serialisiert oder kanonisiert. `signature.value` darf base64url, base64 oder hex sein (das Schema sagt nur
   „string“).
4. Erst danach wird `signed_payload` gelesen (JSON-Text oder base64url davon). Doppelte Schlüssel und NaN/Infinity
   werden abgelehnt.
5. Diese Felder **müssen signiert sein**: `schema_version`, `updater_protocol`, `product`, `platform`, `channel`,
   `version`, `sequence`, `issued_at`, `expires_at`, `download_url`, `sha256`, `size_bytes`,
   `unattended_install_allowed`, `install_mode`; für die Installation in der App zusätzlich `portable`.
6. Jedes Feld, das außen **und** innen steht, muss gleich sein. Verwendet werden ausschließlich die signierten Werte.
7. Danach die Inhaltsprüfung: Produkt, Plattform, Kanal, Version X.Y.Z, Sequenz, Zeiten mit Zeitzone und
   `expires_at > issued_at`, Download-Adressen HTTPS auf `akeno.online` ohne Query, sichere Dateinamen (`.exe`,
   `.zip`), Hashes, Größen und Grenzen wie im Schema.
8. **Abgelaufen** (`expires_at` in der Vergangenheit) → abgelehnt. Die Uhr dafür geht nie hinter die neueste schon
   gesehene signierte `issued_at`-Zeit zurück (eine zurückgestellte PC-Uhr macht nichts wieder gültig).
9. **Sequenz** (`UpdateService.check`): nie kleiner als die zuletzt gesehene; dieselbe Sequenz muss dasselbe Release
   sein (Version, `request_id`, Paket-SHA-256), sonst abgelehnt. **Version** nie kleiner als die höchste gesehene.

Keine Einstellung, Umgebungsvariable oder Oberfläche schaltet diese Prüfung ab. Es gibt keinen Fallback ohne Signatur.
Der Client enthält nur öffentliche Schlüssel (`tools/check_build_config.py` prüft jeden Build darauf).

**Authenticode** (optional): `not_signed`, `present_unverified` und `mixed` blockieren nicht; `invalid` blockiert die
Installation in der App. Ist die laufende AKENO.exe selbst signiert, muss die neue denselben Signierer haben
(bestehende Regel, unverändert).

**Nicht installierbar in der App, aber angezeigt** (mit Hinweis auf die Download-Seite): `install_mode =
manual-installer`, ein anderes `updater_protocol`, `authenticode.status = invalid` des Pakets, installierte Version
kleiner als `minimum_supported_version`.

`unattended_install_allowed: true` wird akzeptiert, ändert aber nichts: AKENO fragt immer.

## 4. Wie ein Update installiert wird

| # | Schritt | Wo |
|---|---|---|
| 1 | Prüfung beim Start (Option, nicht öfter als stündlich) und alle 6 h (Option) – nur Lesen | `UpdatesPage.auto_check` |
| 2 | Anzeige: Version, Datum, Größe, Notizen, Signatur- und Prüfstatus; Benachrichtigung einmal pro Version | Updates-Seite |
| 3 | „Update herunterladen“ (optional automatisch im Hintergrund): Speicherplatz prüfen, Download mit Fortschritt, abbrechbar, fortsetzbar, SHA-256 und Größe = signierte Werte | `UpdateService.download` |
| 4 | „Jetzt installieren“ → Bestätigungsdialog | Updates-Seite |
| 5 | Entpacken nach `%LOCALAPPDATA%\AKENO\updates\staging\<v>` mit den Regeln aus §6, Inhaltsliste (SHA-256 je Datei) | `staging.extract` |
| 6 | Authenticode-Regel für die neue AKENO.exe | `signature_check` |
| 7 | Desktop-Studio-Runtime wird gebeten, sich zu beenden; der Updater (die **neue** AKENO.exe aus dem Staging) startet | `UpdatesPage._launch` |
| 8 | AKENO beendet sich **erst, wenn der Updater sein Journal geschrieben hat** (sonst bleibt AKENO offen und meldet es) | `updater_started` |
| 9 | Updater: wartet auf das Ende von AKENO, beendet weitere AKENO-Prozesse aus dem Programmordner (15 s höflich, dann hart) | `apply.stop_processes` |
| 10 | Prüft das Staging gegen die Inhaltsliste, prüft freien Platz, kopiert **genau die gelisteten Dateien** nach `.<Ordner>.new`, prüft die Kopie | `apply._run` |
| 11 | RunOnce-Eintrag für die Wiederherstellung; Tausch per zwei Umbenennungen auf demselben Laufwerk; Deinstaller und portabler Datenordner werden übernommen | `apply.switch` |
| 12 | Health-Check (§5) | `apply.health` |
| 13 | Erfolg: vorherige Version → `updates\previous`, RunOnce weg, „Installierte Apps“ zeigt die neue Version, Journal „committed“ | `apply.keep_previous` |
| 14 | Fehler: neue Version beenden, vorherige zurück, vorherige starten | `apply._roll_back` |

**Windows-Dateisperren:** Ein laufendes Programm ersetzt seine eigenen Dateien nie; der Updater läuft aus dem
Staging. Geöffnete Dateien (Virenscanner, Explorer) werden mit Backoff bis zu 60 s erneut versucht (Fehler 5, 32,
33, 145, 1224). Mehrfach gestartete AKENO-Fenster und die Desktop-Runtime werden vor dem Tausch beendet.
Gleichzeitige Updates verhindert ein Lock (`prepare.lock`, `apply.lock`; Locks toter Prozesse werden übernommen).
Keine Adminrechte, keine Elevation: Installation pro Benutzer unter `%LOCALAPPDATA%\Programs`. Liegt AKENO in
einem nicht beschreibbaren Ordner (z. B. Program Files), bietet die App die Download-Seite an.

## 5. Rollback, Journal, Health-Check

**Health-Check:** Die neue Version wird mit `AKENO_UPDATE_MARKER` und `AKENO_UPDATE_EXPECT` gestartet. 3 s nachdem
ihr Fenster läuft, schreibt sie einen Bericht (`write_health_report`), der nur liest und nichts verändert:

| Prüfung | Bedeutung |
|---|---|
| `version` | sie ist wirklich die erwartete Version |
| `program_data` | `i18n_de.json`, `distribution.json`, `licensing.json`, `release-keys.json` vorhanden |
| `release_keys`, `license_keys` | Prüfschlüssel geladen |
| `settings_database` | Einstellungen-DB öffnet read-only, `PRAGMA quick_check = ok` |
| `license_state` | `license\state.bin` lässt sich entschlüsseln (DPAPI); die Datei wird dabei nie verschoben oder umgeschrieben |
| `window` | der Bericht kommt aus der laufenden Ereignisschleife nach dem Fenster |

Der Updater verlangt den Bericht mit `ok` und richtiger Version innerhalb von 120 s **und** dass der Prozess danach
noch 5 s läuft. Ein bloß gestarteter Prozess zählt nicht. Die vorherige Version wird erst danach zum Backup.

**Journal** `%LOCALAPPDATA%\AKENO\updates\journal.json` (atomar mit fsync geschrieben, vor jedem Schritt):
`prepared → stopping → copying → swapping → swapped → health → committing → committed` bzw.
`→ rolling_back → rolled_back`.

**Wiederaufnahme** nach Stromausfall, Absturz oder Neustart – durch den RunOnce-Eintrag bei der nächsten Anmeldung
oder beim nächsten Start von AKENO (der übergibt an den Updater im Staging und beendet sich sofort):

| Unterbrochen in | Zustand | Aktion |
|---|---|---|
| `prepared`, `stopping`, `copying` | Programmordner unverändert | `.new` entfernen |
| `swapping`, `swapped`, `health`, `rolling_back` | evtl. getauscht, unbestätigt | `.old` zurück, `.new`/`.failed` entfernen |
| `committing` | neue Version war gesund | Abschluss: `.old` → `updates\previous` |

Jeder Schritt ist idempotent (zweimal ausgeführt = einmal). Während eines Tausches fehlt der Programmordner nur
zwischen zwei Umbenennungen; genau dafür gibt es `.old` und die Wiederherstellung.

**Manueller Rollback:** „Zurück zu <Version>“ tauscht die aufbewahrte Version auf demselben Weg zurück (mit
Health-Check). Die aufbewahrte Version bleibt auch nach späteren fehlgeschlagenen Update-Versuchen bekannt.

**Übernahme des Codex-Rollback-Patches:** nicht möglich, die Übergabe liegt nicht vor (§11). Die Umsetzung oben
erfüllt die Anforderungen des Auftrags; ein Patch kann danach dagegen geprüft werden.

## 6. Schutz gegen Update-Angriffe

| Angriff | Abwehr | Test |
|---|---|---|
| Manipulierte Metadaten | Signatur über `signed_payload`; außen ≠ innen → Ablehnung; Pflichtfelder signiert | `test_signature_and_metadata_manipulation_is_refused` |
| Ungültige/fremde Signatur, Lizenzschlüssel | nur eingebaute Release-Schlüssel | dito, `test_license_keys_never_verify_v2_releases`, E2E |
| Manipuliertes ZIP / Installer | SHA-256 + Größe signiert, Prüfung beim Streamen, Verwerfen | `test_a_manipulated_package_is_never_staged`, E2E |
| ZIP-Slip, Pfad-Traversal, Laufwerksbuchstaben, ADS, Gerätenamen, Punkte/Leerzeichen am Ende | `staging.safe_name` | `test_unsafe_names_are_refused` (15 Fälle) |
| Symlinks, Junctions, Sonderdateien, verschlüsselte Einträge | Entpacken lehnt ab; Staging-Pfad und Baum ohne Reparse-Points | `test_links_*` |
| ZIP-Bombe, falsch deklarierte Größen | signierte Grenzen, echte Bytes gezählt | `test_zip_bombs_and_lying_archives_are_stopped` |
| Beschädigtes Archiv | CRC/Format → Ablehnung | `test_a_damaged_archive_is_refused` |
| Sequenz-Rollback, Replay, Equivocation | Sequenz-Regeln (§3) | `test_sequence_replays_and_downgrades_are_refused` |
| Downgrade | Version nie kleiner als gesehen/installiert | dito |
| Abgelaufene Metadaten, Uhr zurückgestellt | `expires_at` + monotone Uhr | `test_expired_information_…` |
| Unzulässige Umleitungen | jede Umleitung gegen die Host-Liste | `test_release_information_and_package_over_real_https` |
| Manipulation lokaler Staging-Dateien, TOCTOU | Inhaltsliste: Staging vor dem Kopieren geprüft, nur gelistete Dateien kopiert, Kopie vor dem Tausch geprüft | `test_staged_files_changed_after_verification_…` |
| Parallele Updates | Locks | `test_two_updaters_never_run_at_once`, `test_only_one_update_is_prepared_at_a_time` |
| Privilegieneskalation | keine Elevation, pro Benutzer, nicht beschreibbarer Ort → kein Update aus der App | bestehende Tests |
| Stille Installation | keine; Bestätigung fest eingeschaltet | `test_unattended_install_allowed_never_skips_…` |

Grenze: Ein Angreifer, der schon Code als derselbe Windows-Benutzer ausführt, kann das Programm ohnehin verändern;
dagegen schützt kein Updater. Die Prüfungen schützen gegen Netz, Server, Downloads und Fremdeinflüsse auf Dateien.

## 7. Benutzerdaten

Ein Update verändert nur den Programmordner. Unberührt bleiben `%LOCALAPPDATA%\AKENO` mit Lizenz (`license\`),
Sitzung, Einstellungen und Spielbibliothek (`akeno.sqlite3`), Backups, Desktop Studio (Wallpaper, Dock, Widgets,
Themes), Cloud-Sync-Status, Logs, sowie Verknüpfungen (sie zeigen auf denselben Pfad) und die Autostart-Einträge.
Eine Migration war nicht nötig: Es lagen keine Benutzerdaten im Programmordner. Ein portabler Datenordner im
Programmordner und `portable.ini`/`AKENO.ini` werden übernommen; der Inno-Deinstaller (`unins000.*`) ebenfalls,
sodass „Deinstallieren“ nach einem Update weiter funktioniert.

## 8. Lizenzsystem

Unverändert (kein Code im Lizenzpaket geändert, kein Serveraufruf hinzugefügt). Nach einem Update liest die neue
Version denselben DPAPI-Zustand mit derselben Gerätekennung (`MachineGuid`); der Health-Check verlangt, dass sie ihn
entschlüsseln kann, sonst Rollback. Keine erneute Aktivierung. Die Lizenztests laufen unverändert grün.

## 9. Oberfläche

ACCOUNT → **Updates** („AKENO – Updates“), aus den vorhandenen Karten, Status-Pills und Rastern gebaut, ohne
Pop-ups und ohne Animationen; Downloads laufen im Hintergrund:

* Karte „Verfügbares Update“: Status, Version, Kanal, Datum, Größe, Auszug der Versionshinweise, Fortschritt;
  „Jetzt installieren“, „Update herunterladen“, „Abbrechen“, „Versionshinweise anzeigen“, „Später erinnern“,
  „Download-Seite“.
* Karte „Dieser PC“: installierte und neueste Version, Kanal, Quelle, letzte erfolgreiche Prüfung,
  Installationsstatus; „Nach Updates suchen“, „Zurück zu …“.
* Karte „Prüfung“: Release-Signatur, Release-Informationen (Sequenz, gültig bis), Paket-Prüfsumme, Paketinhalt,
  Code-Signatur.
* Karte „Optionen“: automatisch suchen, beim Start suchen, im Hintergrund herunterladen, „Vor der Installation immer
  fragen“ (fest an). Dieselben Optionen in Einstellungen → Updates, mit Link zur Seite.

Alles auf Deutsch und Englisch.

## 10. Werkzeuge

* `tools/release_v2.py describe <zip> <exe>` – die Werte, die das signierte Release tragen muss (SHA-256, Größen,
  Dateianzahl, entpackte Größe, PE-Dateien), geprüft mit den Entpackregeln des Updaters.
* `tools/release_v2.py verify <release.json> --zip … --exe …` – prüft ein signiertes Release mit genau dem Parser der
  App gegen die gebauten Dateien, offline, ohne Schlüssel.
* `tools/api_contract.py --schemas --api-version v2` (Produktionscheck) – liest das Schema live; sobald `/releases/stable`
  ein Release liefert, verifiziert der Check es mit dem App-Parser und zeigt Sequenz, Ablauf und Installationsweg.
* `tools/update_e2e.py` – der Windows-Ende-zu-Ende-Test (§12), nur für Wegwerf-Maschinen.

## 11. Offene Punkte (Backend / Codex) – bitte bestätigen

Keine Backend-Änderung wurde vorgenommen. Diese Punkte sind im OpenAPI-Dokument nicht festgelegt; der Client ist so
gebaut, dass eine Abweichung **sicher fehlschlägt** (Release wird nicht angeboten), nicht unsicher:

1. **Signierte Bytes:** Der Client prüft Ed25519 über die exakten UTF-8-Bytes von `signed_payload`. Falls die
   Signatur über etwas anderes läuft (z. B. die base64-dekodierten Bytes oder ein Präfix), lehnt AKENO jedes Release ab.
   → Bitte bestätigen oder Format nennen. Schnellster Nachweis: `tools/release_v2.py verify` auf die JSON-Antwort
   eines (Test-)Releases anwenden.
2. **Felder im `signed_payload`:** Der Client verlangt die in §3 Punkt 5 genannten Felder **innerhalb** von
   `signed_payload`, für In-App-Updates zusätzlich `portable`. → Bitte bestätigen, dass `sequence`, `issued_at`,
   `expires_at`, `install_mode` und `portable` mitsigniert werden.
3. **`content_sha256`:** Der Algorithmus ist nicht dokumentiert. Der Client prüft ihn **nicht** und verlässt sich auf
   den signierten SHA-256 des ganzen ZIPs plus seine eigene Inhaltsliste je Datei. → Bitte den Algorithmus nennen
   (Reihenfolge, Pfadform, Trennzeichen, Hash je Datei oder über Bytes); dann wird er als zusätzliche Prüfung ergänzt.
4. **`pe_files`:** Der Client zählt PE-Dateien (MZ + `PE\0\0`) nur zur Anzeige; keine Erzwingung.
5. **`authenticode.verified` vs. `status`:** nur `status` wird ausgewertet (`invalid` blockiert).
6. **Dateinamen:** Der Auftrag nennt `AKENO-Setup.exe` / `AKENO-Portable.zip`. Das bestehende Build- und
   Release-System erzeugt `AKENO-Gaming-Suite-<v>-Setup-x64.exe` / `AKENO-Gaming-Suite-<v>-portable-x64.zip`. Der Client
   ist namensunabhängig (nur `.exe`/`.zip` und sichere Zeichen). → Welche Namen erwartet das Admin-Panel?
7. **`install_mode`:** Für In-App-Updates muss das Release `portable-directory-swap` mit `portable` sein. Mit
   `manual-installer` zeigt AKENO nur die Download-Seite.

## 12. Release 3.0.0 – nächste Schritte (erst nach deiner Freigabe)

1. **Der bestehende 3.0.0-Entwurf im Admin-Panel darf nicht veröffentlicht werden.** Seine Dateien stammen aus
   Builds vor dieser Änderung: Diese 3.0.0 kann v2-Releases nicht lesen und würde sich nie selbst aktualisieren.
2. Die Dateien dieses Branches nehmen: CI-Lauf „Build and test“ → Artefakt `AKENO-Gaming-Suite-windows-x64`
   (Setup-EXE, portables ZIP, `SHA256SUMS.txt`), nur aus einem Lauf, in dem auch
   „Windows update end to end“ grün ist.
3. Prüfen: `python tools/release_v2.py describe <zip> <exe>` – Werte notieren.
4. Im Admin-Panel den Entwurf mit **diesen** Dateien aktualisieren oder einen neuen anlegen. Weil sich Dateien,
   Hashes und damit die signierten Werte ändern, ist eine **neue Signaturanfrage** nötig (neue `request_id`, neue
   `sequence`); die alte darf nicht wiederverwendet werden. Werte mit Schritt 3 vergleichen.
5. Lokal signieren (privater Schlüssel nur in deinen Tresoren).
6. Vor dem Veröffentlichen: die vom Admin-Panel erzeugte Release-JSON mit
   `python tools/release_v2.py verify release.json --zip … --exe …` prüfen (muss „signature valid“ und zwei
   „[OK]“ für die Dateien zeigen).
7. Veröffentlichen. Danach GitHub Actions → „Production read-only check“ → „Run workflow“: er verifiziert das
   veröffentlichte Release mit dem App-Parser.
8. Für 3.0.0 selbst ist `install_mode` egal (es gibt keine ältere v2-fähige Version). Ab 3.0.1 muss das Release
   `portable-directory-swap` mit `portable` sein, damit sich 3.0.0 selbst aktualisiert.

## 13. Geänderte und neue Dateien

| Datei | Änderung |
|---|---|
| `akeno/updater/api_v2.py` | neu: Schema-2-Parser |
| `akeno/updater/staging.py` | neu: sicheres Entpacken, Inhaltsliste, Prüfung, atomares Schreiben |
| `akeno/updater/apply.py` | neu geschrieben: Journal, Prozesse beenden, geprüftes Kopieren, Tausch, Health-Check, Rollback, Wiederherstellung, RunOnce, Deinstaller, „Installierte Apps“ |
| `akeno/updater/service.py` | Schema 2, Sequenz-/Ablaufschutz, Speicherplatz, Download/Staging, Plan, Health-Bericht, Updater-Startbestätigung, aufbewahrte Version |
| `akeno/updater/manifest.py` | Weiterleitung an Schema 2 |
| `akeno/security/downloads.py` | Größengrenze pro Manager (Updates bis 600 MiB) |
| `akeno/app.py` | `--recover-update`, Übergabe beim Start, Health-Bericht, Ergebnis erneut melden |
| `akeno/ui/pages/updates.py` | neue Updates-Seite |
| `akeno/ui/pages/settings.py`, `akeno/core/settings.py` | Update-Optionen |
| `akeno/data/i18n_de.json` | deutsche Texte |
| `tools/release_v2.py`, `tools/update_e2e.py` | neu |
| `tools/api_contract.py`, `.github/workflows/production-check.yml` | Schema-Ausgabe, v2-Details |
| `.github/workflows/build.yml` | E2E-Job, Screenshots, Veröffentlichung wartet auf E2E |
| `tests/test_updater_v2.py` | neu (58 Tests) |
| `tests/test_updater.py`, `tests/test_updater_vps.py` | an Health-Bericht und Phasen angepasst |
| `tests/test_account_client.py` | Test-Server liest den Request-Body (Wackeltest unter Windows behoben) |
| `docs/…` | diese Dateien, Release Notes |
