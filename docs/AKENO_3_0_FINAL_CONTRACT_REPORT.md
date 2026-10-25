# AKENO 3.0.0 – Abschlussbericht Vertragskonformität (Codex-Vertrag vom 2026-10-25)

Stand 2026-10-25. Nichts veröffentlicht, nichts gemergt, kein Upload, kein Release-Entwurf angefasst. VPS,
Lizenzsystem und produktiver Ed25519-Schlüssel `d6139235b220a4aa` sind unverändert. Kein privater Schlüssel wurde
angefordert oder verwendet.

Ersetzt §0, §4, §8 und §9 von `AKENO_3_0_FINAL_CLIENT_ALIGNMENT.md`: Dort fehlte die Codex-Übergabe. Jetzt lag sie vor.

## 1. Eingang geprüft

| Datei | Ergebnis |
|---|---|
| `AKENO_3_0_FINAL_API_CONTRACT.md` | vollständig lesbar (474 Zeilen), identisch mit der Kopie im Bundle |
| `AKENO_3_0_API_CONTRACT_BUNDLE.zip` | vollständig entpackbar, `SHA256SUMS.txt`: **59/59 OK** |

Das Bundle wurde als nicht vertrauenswürdig behandelt: Es wurde in einen eigenen Ordner entpackt, Python lief
isoliert (`-I`), und es wurde kein Skript aus dem Bundle im Repository ausgeführt. Übernommen wurden **nur**
geprüfte öffentliche Testdaten nach `tests/fixtures/api_contract/` (README dort). Das sind die fünf
Vektor-ZIPs, die exakten kanonischen Record-Bytes, `vector-index.json`, der Beispiel-Umschlag und der öffentliche
Testschlüssel. Alle Hashes werden im Test gegen das `SHA256SUMS.txt` des Bundles geprüft.

**Nicht** übernommen: `source-evidence/` (Backend- und Client-Quellen), Logs, Skripte und `expected.json`
(redundant zum Index). Auch `installer.exe` wurde nicht übernommen. Die `AKENO.exe` in den Vektoren ist ein
1024-Byte-PE-Header mit 42 Nicht-Null-Bytes, kein Programm.

## 2. Vergleich 413d4ef/273e003 gegen den Vertrag

Grundlage sind `release_protocol.validate`/`verify` und `portable.py` des Backends (gelesen, nicht committet).
Als Probe liefen die neuen Tests gegen den alten Client (273e003). Ergebnis: **23 Tests rot**. Darunter waren 2 der
14 dokumentierten Strenge-Unterschiede; die übrigen 12 hatte 413d4ef schon geschlossen.

| # | Abweichung in 273e003 | Jetzt (Backend-gleich) |
|---|---|---|
| 1 | Installer-/Portable-URL: beliebiger Pfad auf `akeno.online` | **genau** `https://akeno.online/downloads/<version>/AKENO-Setup.exe` bzw. `…/AKENO-Portable.zip` (`different_trusted_installer_path`) |
| 2 | `minimum_supported_version` > Version erlaubt | null oder ≤ Version (`minimum_above_release`) |
| 3 | `updater_protocol` ≠ 2 nur „nicht in der App installierbar“ | genau Integer 2, sonst abgelehnt |
| 4 | ohne `portable`: `unattended_install_allowed: true` erlaubt | ohne Portable nur `manual-installer` und nicht unbeaufsichtigt |
| 5 | mit `portable` und `manual-installer` erlaubt | mit Portable nur `portable-directory-swap` |
| 6 | `published_at`/`issued_at` als Zeitpunkt verglichen | als Text verglichen (`…Z` ≠ `…+00:00`) |
| 7 | Zeitstempel beliebig lang | höchstens 40 Zeichen |
| 8 | `release_date` in der Zukunft erlaubt | ISO-Datum mit Rundlauf, nicht nach dem heutigen UTC-Tag |
| 9 | `release_notes` nur aus Leerzeichen erlaubt | `strip()` nicht leer, höchstens 8000 Zeichen |
| 10 | Regexe mit `$` (akzeptierten ein abschließendes `\n`) | `fullmatch` (Version, Request-ID, Hashes, Key-ID) |
| 11 | Authenticode: `publisher_certificate_sha256` durfte fehlen; verifiziert ohne Pin; unverifiziert mit Pin | genau 3 Felder; verifiziert ⇔ Status `verified` **und** Pin; sonst Pin `null` |
| 12 | verifiziertes Portable-Paket mit `verified_pe_files < pe_files` | gleich |
| 13 | `signature.value` auch hex/base64 mit Padding | nur kanonisches base64url ohne Padding, 64 Byte |
| 14 | `signed_payload` auch base64url-kodiertes JSON | nur JSON-Text, höchstens 65 536 UTF-8-Bytes |
| 15 | Feld außen ohne Gegenstück innen erlaubt, sofern bekannt (z. B. `portable: null`) | jedes äußere Feld muss signiert und gleich sein (`unsigned_unknown_outer_field`) |
| 16 | `key_id` nur nachgeschlagen | zusätzlich `key_id` = SHA-256(Schlüssel)[:16] |
| 17 | Client-eigene Regel `pe_files ≤ file_count` | entfernt (das Backend kennt sie nicht; echte Releases erfüllen sie ohnehin) |
| 18 | Entpacken: Eintrag mit DOS-Verzeichnisattribut ohne `/` galt als Ordner | Ordner nur bei `ZipInfo.is_dir()`, genau wie der Inhaltsindex des Backends |
| 19 | Backslash wurde zu `/` umgeschrieben | Backslash, nicht-NFC-Namen und NUL abgelehnt (das Backend veröffentlicht solche Pakete nie) |
| 20 | Authenticode-Upgradepfad nur über den Signer-Namen | zusätzlich: Nennt das Paket einen verifizierten Herausgeber, muss die neue AKENO.exe gültig mit **genau diesem Zertifikat** signiert sein (SHA-256 des DER-Leaf, wie `osslsigncode -require-leaf-hash`) |

Seit 413d4ef geschlossen und weiter gültig: `content_sha256` wird in `UpdateService.stage` direkt nach
`staging.extract` nachgerechnet und verglichen, vor `program_root`/`relative_to_root`. `file_count` und entpackte
Größe sind exakt gleich. Die sechs fehlenden Pflichtfelder sind Pflicht. `schema_version: 2.0` wird abgelehnt.
Ebenso abgelehnt werden: mehr als 5 min in der Zukunft, mehr als 30 Tage Gültigkeit, `expires_at == now` und
unbekannte Portable-Felder.

Gleich geblieben, weil schon vertragsgleich: Ed25519 über die exakten UTF-8-Bytes, doppelte Schlüssel und
NaN/Infinity abgelehnt, Sequenz- und Versionsschutz (Anti-Replay bleibt aktiv), keine Installation ohne
„Installieren“ und Bestätigung.

Bewusst strenger als das Backend (kann ein echtes Release nicht ablehnen):
* Außen und innen werden als JSON verglichen (`1` ≠ `true`).
* Downloads müssen zusätzlich auf einem Host des Builds liegen.

## 3. Die fünf offiziellen `content_sha256`-Vektoren (`tests/test_api_contract.py`)

| Vektor | erwartet | Client (`staging.extract`) |
|---|---|---|
| 01 Text und Unicode | `012a7b9c…5c103cf` | gleich, Bytes = `canonical-records.utf8.json` |
| 02 gültiges Portable | `715885f8…b9996fe` | gleich |
| 03 andere Eintragsreihenfolge | `715885f8…b9996fe` | gleich (Reihenfolge egal) |
| 04 Oberverzeichnis `AKENO/` | `9482b2cf…6198522` | gleich; ohne Präfix ergäbe sich 02 (Präfix zählt mit) |
| 05 Unicode-Skalarordnung | `f1e6419d…c15350` | gleich; UTF-16-Sortierung ergäbe einen anderen Wert, UTF-8-Bytes denselben |

Für jeden Vektor wird geprüft:
* ZIP-SHA-256 und -Größe;
* Dateianzahl und entpackte Größe;
* jeder Record;
* die kanonischen Bytes (Text und Hex) und der Dateiinhalt jeder Eingabe.

Vier Vektoren laufen zusätzlich durch `UpdateService.stage`: 02, 03, 04 und 05. Mit falschem `content_sha256`
(auch dem Wert mit Präfix), falscher Dateianzahl oder falscher Größe wird abgelehnt, und das Staging wird gelöscht.

## 4. Ed25519 gegen das öffentliche Testbeispiel

`example-test-envelope.json` (Testschlüssel `4f0b6765af0b6fa0`, nur als Argument übergeben):
* Bei fester Zeit 2026-10-25 12:01 UTC wird es akzeptiert.
* `signed_payload` entspricht Byte für Byte `canonical(...)` der signierten Werte.
* Abgelehnt werden: der produktive Schlüsselbund des Builds („does not trust“), ein angehängtes Leerzeichen
  („signature does not match“), der Zeitpunkt nach Ablauf und mehr als 5 min vor `issued_at`.

Der Testschlüssel gelangt nicht in den produktiven Build:
* `akeno/data/release-keys.json` ist unverändert und enthält nur `d6139235b220a4aa`.
* `tools/check_build_config.py` lehnt einen Build jetzt ab, wenn er den Testschlüssel vertraut, den öffentlichen
  Testschlüssel oder den Beispieltext enthält. Der Test dazu ist `test_build_check_refuses_the_contract_test_key_…`.
  Die CI prüft so das Programmverzeichnis und das Portable-ZIP.
* `tests/` wird nicht gepackt (`packaging/akeno.spec`: nur `akeno/data`).

## 5. Übernommene Cross-Contract-Tests

Alle Fälle aus `test_cross_contract.py` und `validation-strictness-differences.json`, die ohne Backend-Code laufen,
sind übernommen:
* exakte Bytes und Außen/Innen-Abweichung;
* alle 6 Sicherheitsfelder durch die Signatur gedeckt;
* doppelte Schlüssel;
* die 14 Strenge-Unterschiede (jetzt alle abgelehnt);
* Sequenz- und Replay-Regeln;
* `content_sha256` vor dem Root-Strip (früher „wird nicht verglichen“, jetzt umgekehrt geprüft);
* weitere 76 Einzelregeln aus `validate`/`verify`, jeweils mit gültiger Wegwerfsignatur und erwartetem
  Backend-Ergebnis.

Nicht übernommen ist `test_exact_production_routes_accept_ci_names_and_client_parser`: Er braucht den Backend-Code
und die Admin-Oberfläche, und beides gehört nicht in dieses öffentliche Repository.

## 6. Tests

| Wo | Ergebnis |
|---|---|
| lokal (Linux, offscreen), Commit `38680ac` | **1039 bestanden**, 13 übersprungen (Windows-only u. a.) |
| CI **Run 187** (`40240402049`, Commit `e34a951`): Linux-Tests, Windows-Tests, Lizenzserver (SQLite/PostgreSQL/Docker), Real-Assets, Windows-Build inkl. Build-Konfigurationsprüfung (nur `d6139235b220a4aa`), Smoke-Tests, Desktop Studio, neuer Schritt „Release Manager values“ | **alle grün**; Veröffentlichungsjobs übersprungen (wie vorgesehen) |
| Windows-Update-E2E Run 187 (echter Installer, echte EXE, lokale Test-Release-API mit den exakten `/downloads/<version>/AKENO-Portable.zip`-Pfaden) | **57 bestanden, 0 fehlgeschlagen** |

Erhalten (durch Suite und E2E belegt):
* Lizenzsystem (unverändert, keine Änderung an `akeno/licensing`), Wallpaper, Themes, Einstellungen;
* Auto-Update mit Bestätigung, separater Updater, Health-Check, Rollback (automatisch und manuell),
  Stromausfall-Recovery, Deinstallation.

## 7. Release-Kandidat

Run 187 (`40240402049`), Commit `e34a951cf809204c25afbbe0903d6d33cbcab21e`, Artefakt
`AKENO-Gaming-Suite-windows-x64` (ID `12616159414`, Archiv-Digest
`sha256:4dc8fa652e9715d68ddfd5beb617b863bbf62788cd02fb118585ebd481461acb`):

```
fe919b720e7db4a7e3e8fce97555bb77cde23851d205de3f039d847b1e0dd20f  AKENO-Gaming-Suite-3.0.0-Setup-x64.exe
d4c5701988f9f21841e1bd8a2c059c32e02b6e1de3f50180849837b7eb95e9f9  AKENO-Gaming-Suite-3.0.0-portable-x64.zip
cf6919c3c45d65b6fcd03470ee1f3954d22d910296c0dd7c2f812d30661514f2  AKENO-community-dependencies-3.0.0-x64.zip
```

Release-Manager-Werte (CI-Schritt „Release Manager values“, mit den Entpackregeln des Updaters berechnet):

| | Wert |
|---|---|
| Installer | 157 148 888 Byte |
| Portable-ZIP | 222 769 069 Byte |
| `file_count` | 2906 |
| `uncompressed_size_bytes` | 493 082 178 |
| `pe_files` (lokal gezählt) | 355 |
| Oberverzeichnis | keins |
| `content_sha256` | `b1f902a1b94af1def73a3f50b481f562371562f1fab19dad398934a4f307ad1f` |
| innerhalb der Release-Manager-Grenzen | ja |

Jeder neue CI-Lauf baut neue, nicht bytegleiche Dateien. Für eine Signatur genau **diese** Dateien aus Run 187
verwenden. Der Doku-Folgecommit mit diesem Bericht ändert keinen Code, sein Build erzeugt aber andere Hashes.
Ältere Kandidaten (Run 182/184) entsprechen dem Vertrag nicht vollständig und dürfen nicht mehr verwendet werden.

## 8. Offene Punkte und Risiken

1. Ein echter, mit `d6139235b220a4aa` signierter V2-Umschlag existiert noch nicht (Schlüssel offline, keine
   Freigabe). Der Client ist gegen Backend-Regeln und Testsignatur geprüft, nicht gegen eine echte Produktionsantwort.
2. Kein Windows-Lauf gegen einen echten veröffentlichten VPS-Download (Caddy). Der E2E nutzt eine lokale HTTPS-Kopie
   der Routen.
3. Der Herausgeber-Pin ist implementiert und unit-getestet; `leaf_sha256` wird auf Windows gegen
   das selbst aus der PE-Datei gelesene PKCS#7-Signer-Zertifikat geprüft (signierte `python.exe`). Ein Update mit einem echten AKENO-Zertifikat ist
   ungetestet, weil keines existiert.
4. Server-„Restore as Latest“ auf ältere Releases bleibt für Clients, die Neueres gesehen haben, absichtlich nicht
   installierbar (Anti-Replay). Korrektur nur über einen höher versionierten Build.
5. Der Kandidat ist 3.0.0. Für ein echtes In-App-Update braucht es danach einen 3.0.1-Build.
6. Praktisch ungetestet: Windows 11 auf echter Hardware, Neustart mit RunOnce, Klick in der echten Oberfläche,
   Lizenzaktivierung mit echtem Konto.

## 9. Produktive Schritte (nur nach deiner ausdrücklichen Freigabe)

1. Artefakt `AKENO-Gaming-Suite-windows-x64` aus dem Lauf in §7 laden, `SHA256SUMS.txt` gegen §7 prüfen.
2. `python tools/release_v2.py describe <portable.zip> <Setup.exe>` – die Werte müssen dem CI-Schritt
   „Release Manager values“ entsprechen.
3. Im Admin-Panel einen **neuen** Entwurf mit genau diesen Dateien anlegen (Upload-Namen aus der CI sind zulässig).
   Den vorhandenen unsignierten Entwurf nicht verwenden. Danach neue Signaturanfrage stellen und offline signieren.
4. Vor dem Veröffentlichen: `python tools/release_v2.py verify release.json --zip … --exe …` muss überall „OK“ zeigen.
5. Veröffentlichen, danach „Production read-only check“.
