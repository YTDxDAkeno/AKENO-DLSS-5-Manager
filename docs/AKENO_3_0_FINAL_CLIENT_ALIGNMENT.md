# AKENO 3.0.0 – Final Client Alignment

Stand 2026-10-25. Nichts veröffentlicht, nichts gemergt, kein produktiver Upload, kein Release-Entwurf angefasst,
VPS, Lizenzsystem und produktiver Ed25519-Schlüssel unverändert.

## 0. Wichtig vorab: die Codex-Übergabe lag nicht vor

`AKENO_3_0_FINAL_API_CONTRACT.md` und `AKENO_3_0_API_CONTRACT_BUNDLE.zip` sind in dieser Sitzung **nicht
angekommen** (weder im Arbeitsverzeichnis noch in einem Branch noch per Code-Suche). Deshalb:

* Umgesetzt ist, was der Auftrag selbst eindeutig festlegt: der content_sha256-Algorithmus, die Pflichtfelder, die
  Zeitgrenzen, die JSON-Typregeln, die Feldstrenge (das Live-Schema sagt `additionalProperties: false`), die
  Authenticode-Konsistenz.
* **Nicht** geprüft: die fünf Hash-Testvektoren, die Backend-Quellbelege, die Cross-Contract-Tests und die
  `SHA256SUMS.txt` des Pakets. Der Platzhaltertest `test_codex_hash_vectors` ist ausdrücklich übersprungen.
* Nichts davon wurde erfunden. Wo nur der Bericht Details kennt (z. B. die genauen Release-Downloadpfade), ist das
  unter „Verbleibende Unterschiede“ aufgeführt.

## 1. Commits

| | Commit |
|---|---|
| Ausgangsstand (50/50 E2E, Run 182) | `7add76eea18e5f0157955876966c30c6d7c1a2a4`, Doku danach `cceb39f2e8f44ab5799cbb5fdc0e62b4f3fe1cee` |
| Vertragsangleichung + getesteter Build (Run 184, `40231647101`) | `413d4effff54c81c2af48b13e7c6bc1cc8fb2b69` |
| Dieser Bericht | Folgecommit (nur Doku) |

## 2. Geänderte Dateien

| Datei | Änderung |
|---|---|
| `akeno/updater/api_v2.py` | Pflichtfelder, unbekannte Felder (signiert, Hülle, `portable`, `authenticode`, `signature`), strikte JSON-Ganzzahlen, UTC-Zeiten und Zeitgrenzen, `published_at == issued_at`, `release_date`/`release_notes`/`request_id`, Authenticode-Konsistenz, `key_id`-Format |
| `akeno/updater/manifest.py` | `schema_version` nur als echte JSON-Ganzzahl (Schema-1-Pfad akzeptierte `true`) |
| `akeno/updater/staging.py` | `content_sha256()` und Erfassung der Originalnamen beim Entpacken |
| `akeno/updater/service.py` | `stage`: exakter Vergleich von Dateianzahl, entpackter Größe und `content_sha256` direkt nach dem Entpacken |
| `akeno/ui/pages/updates.py`, `akeno/data/i18n_de.json` | ehrliche Anzeige des Inhaltsabgleichs (DE/EN) |
| `tools/release_v2.py` | berechnet und vergleicht `content_sha256` |
| `tools/update_e2e.py` | Releases mit echtem Aggregat (unabhängige Implementierung), Unicode-Datei, drei neue Ablehnungsszenarien |
| `tests/test_updater_v2.py`, `tests/test_updater_release_manager.py` | neue Vertrags-, Grenzwert- und Negativtests |

## 3. Behobene Unterschiede zum Backend

| Bereich | Vorher | Jetzt |
|---|---|---|
| `content_sha256` | nicht geprüft | pro regulärer ZIP-Datei `{name (wie gespeichert, inkl. Oberverzeichnis, nicht normalisiert), sha256, size_bytes (tatsächlich)}`, nach Codepunkt sortiert, `json.dumps(…, ensure_ascii=False, separators=(",", ":"), sort_keys=True)` als UTF-8 ohne BOM/Zeilenumbruch, SHA-256; Vergleich in `UpdateService.stage` nach `staging.extract`, vor der Root-Behandlung; Abweichung → Abbruch, Staging gelöscht |
| `file_count`, `uncompressed_size_bytes` | nur Obergrenzen | zusätzlich exakt gleich |
| Pflichtfelder | `request_id`, `release_date`, `release_notes`, `published_at`, `minimum_supported_version`, `authenticode` durften fehlen | müssen signiert vorhanden sein (`minimum_supported_version` darf `null` sein) |
| Unbekannte Felder | ignoriert | abgelehnt (signierter Inhalt, Hülle, `portable`, `authenticode`, `signature`); `portable` braucht alle 9 Felder |
| Typen | `schema_version: 2.0` wurde als 2 akzeptiert; **`schema_version: true` lief als Schema 1 durch** (`True == 1`) | nur echte JSON-Ganzzahlen; außen/innen als JSON verglichen (`7` ≠ `7.0`, `1` ≠ `true`) |
| Zeiten | Zeitzone verlangt, nur „nicht abgelaufen“ | UTC; `issued_at` ≤ jetzt + 5 min; `published_at == issued_at`; Gültigkeit 1 h … 30 Tage; `expires_at` > jetzt; Schutz gegen zurückgestellte Uhr bleibt |
| Authenticode | Status ausgewertet | zusätzlich `verified` muss zu `status` passen (`verified` ⇔ `status == "verified"`); keine Zusatzfelder; ungültig blockiert; unsigniert erlaubt; Publisher-Regel der laufenden EXE unverändert |
| `pe_files`, `verified_pe_files` | angezeigt | weiterhin nur angezeigt: das ist das serverseitige Zählverfahren bzw. Authenticode-Urteil, lokal nicht reproduzierbar spezifiziert; `content_sha256` bindet ohnehin jedes Byte. `verified_pe_files ≤ pe_files ≤ file_count` wird geprüft |

## 4. Die fünf Hash-Testvektoren

**Nicht ausgeführt** – das Bundle lag nicht vor. Stattdessen getestet (alle grün):
* ein fest ausgeschriebener Referenzvektor (die exakten JSON-Bytes von Hand), `test_content_sha256_encoding_is_pinned`;
* eine unabhängige Zweitimplementierung im Test (`reference_content_sha256`) und eine dritte im Windows-E2E-Skript
  (`content_aggregate`), beide gegen den Client;
* Unicode-Namen (ä, 日本/語), Codepunktreihenfolge (Groß- vor Kleinbuchstaben vor ä vor CJK), umgekehrte
  Eintragsreihenfolge (gleiches Aggregat), mit/ohne Oberverzeichnis (verschiedenes Aggregat).

Sobald die Vektoren vorliegen: unter `tests/fixtures/api_contract/` ablegen, `test_codex_hash_vectors` füllen. Weicht
ein Vektor ab, ist die Implementierung entsprechend zu korrigieren.

## 5. Negativtests (alle abgelehnt, alle grün)

Fehlende Pflichtfelder (alle 20 einzeln), unbekannte Felder (signiert, außen, `portable`, `authenticode`,
`signature`), fehlendes `portable`-Feld, `schema_version` `2.0`/`true`/`"1"`, `updater_protocol` `2.0`, `sequence`
`7.0`/`true`, `size_bytes` `true`, `file_count` `3.0`, außen `7.0` vs. innen `7`, `request_id` falsch,
`release_date` ungültig/falsches Format, `release_notes` leer/zu lang, `minimum_supported_version` falsch,
Authenticode widersprüchlich (3 Fälle)/unbekannter Status/falscher Publisher-Hash/`null`, `key_id` in Großbuchstaben,
Zeitgrenzen an beiden Enden (11 Fälle: +5 min ok / +5 min 1 s abgelehnt, 1 h ok / knapp darunter abgelehnt, 30 Tage
ok / 1 s darüber abgelehnt, `expires_at == now` abgelehnt / +1 s ok, `published_at ≠ issued_at`, Nicht-UTC, keine
Zeit), falsches signiertes `content_sha256`, falsche Dateianzahl, entpackte Größe +1 und −1, sowie alle früheren
Fälle (Signatur, Manipulation, Replay, Downgrade, ZIP-Slip, Links, Bomben, Lock, Health-Check, Recovery).

## 6. Vollständige Testsuite

| Wo | Ergebnis |
|---|---|
| lokal (Linux, offscreen) | 913 bestanden, 8 übersprungen (u. a. Codex-Vektoren, Windows-only-Junction) |
| CI Run 184: Linux, Windows-Tests, Lizenzserver (SQLite/PostgreSQL/Docker), Real-Asset-Verträge, Windows-Build inkl. Build-Konfigurationsprüfung, Smoke-Tests, Desktop Studio | alle grün |

## 7. Windows-E2E (Run 184, echter Installer, echte EXE-Dateien)

**57 von 57 bestanden** (vorher 50; neu: Unicode-Datei im echten Update, drei Ablehnungen samt leerem Staging,
Ordner danach unverändert). Auszug der neuen Zeilen:

* Update 3.0.0 → 3.0.1 inkl. `docs/e2e-ünïcode-日本.txt`: 2892 Dateien per SHA-256 identisch
* falsches signiertes `content_sha256` → „the package contents do not match the signed content_sha256“
* falsche Dateianzahl → „the package has 2892 files, the signed release says 2894“
* falsche Größe → „the package unpacks to 464018440 bytes, the signed release says 464018441“
* alle bisherigen Szenarien (Rollback ×3, Stromausfall-Recovery, RunOnce, Manipulation, fremde Signatur, manueller
  Rollback, Deinstallation, Lizenz/Einstellungen/Themes/Wallpaper unverändert) weiterhin OK

## 8. Release-Kandidat (Run 184, Commit `413d4ef`, Artefakt `AKENO-Gaming-Suite-windows-x64`)

```
e1283c256899c9bc35fd601a235c2d736a0f4b288f690fffdea5f1a445334f0f  AKENO-Gaming-Suite-3.0.0-Setup-x64.exe
a1e7fd5eaf899be16a7cc5dda1d7f269c0a5148ade0074fbbcbf8f33fb581e1b  AKENO-Gaming-Suite-3.0.0-portable-x64.zip
78d5a425c7e4a35da165867ca7d8e1478762e79a1044925cc449784b0456f63c  AKENO-community-dependencies-3.0.0-x64.zip
```

`SHA256SUMS.txt` liegt im Artefakt. Der produktive Release-Prüfschlüssel `d6139235b220a4aa` ist unverändert
eingebunden (`akeno/data/release-keys.json` seit Ausgangsstand unverändert; Build-Schritt „Packaged build trusts the
official API and the public license and release keys“ grün). Testschlüssel werden nur im Wegwerf-E2E-Job in die
dortige Testinstallation geschrieben, nie in `dist`. Keine privaten Schlüssel in Artefakten.

**Wichtig:** Jeder neue CI-Lauf baut neue, nicht bytegleiche Dateien. Für die Signatur genau **diese** Dateien aus
Run 184 verwenden (auch der Doku-Folgecommit erzeugt andere Hashes).

## 9. Verbleibende Unterschiede und Risiken

1. **Codex-Bundle nicht geprüft** (§0): Hash-Vektoren, Backend-Belege, Cross-Contract-Tests. Größtes Restrisiko:
   eine Abweichung im Aggregat würde jedes In-App-Update sicher ablehnen (nicht unsicher annehmen).
2. **Release-Downloadpfade**: der Client verlangt HTTPS auf `akeno.online`-Hosts, ohne Query/Fragment, sichere
   Dateinamen (`.exe`/`.zip`); Umleitungen werden pro Sprung auf Host und HTTPS geprüft. Eine Prüfung des
   **Pfadpräfixes** fehlt, weil die dokumentierten Pfade nur im Bericht stehen.
3. **Authenticode-Konsistenz** `verified ⇔ status == "verified"` ist aus den Feldern abgeleitet; gegen die
   Backend-Belege zu bestätigen.
4. Signiert wird über die exakten UTF-8-Bytes von `signed_payload` (unverändert seit Run 182) – gegen ein echtes,
   mit dem produktiven Schlüssel signiertes Release noch ungetestet.
5. **Praktisch ungetestet**: Windows 11 auf echter Hardware, echter Neustart mit RunOnce bei der Anmeldung, der Klick
   in der echten Oberfläche, Lizenzaktivierung mit echtem Konto, Authenticode-signierte Builds.
6. Der parallel auf dem VPS entstandene unsignierte Entwurf passt nicht zu diesen Dateien und darf nicht verwendet
   werden.

## 10. Schritte für den produktiven Release (nur mit deiner Freigabe)

1. Codex-Bundle bereitstellen; Hash-Vektoren einbinden; bei Abweichung korrigieren und neuen Kandidaten bauen.
2. Artefakt `AKENO-Gaming-Suite-windows-x64` aus **Run 184** laden; `SHA256SUMS.txt` gegen §8 prüfen.
3. `python tools/release_v2.py describe AKENO-Gaming-Suite-3.0.0-portable-x64.zip AKENO-Gaming-Suite-3.0.0-Setup-x64.exe`
   – notiert SHA-256, Größen, Dateianzahl, entpackte Größe, `content_sha256`.
4. Im Admin-Panel einen **neuen** Entwurf mit genau diesen Dateien anlegen (den vorhandenen nicht verwenden,
   nichts löschen ohne Entscheidung); die Werte mit Schritt 3 vergleichen; **neue Signaturanfrage** (neue
   `request_id`/`sequence`), keine alte wiederverwenden.
5. Offline signieren.
6. Vor der Veröffentlichung: `python tools/release_v2.py verify release.json --zip … --exe …` – muss „signature
   valid“, „can install it itself“ und für `content_sha256`, `file_count`, `uncompressed_size_bytes` und beide
   Dateien „[OK]“ zeigen.
7. Veröffentlichen, danach „Production read-only check“ starten (verifiziert das Live-Release mit dem App-Parser).
