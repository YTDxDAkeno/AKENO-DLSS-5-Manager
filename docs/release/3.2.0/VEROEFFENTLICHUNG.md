# AKENO Gaming Suite 3.2.0 – Veröffentlichung als Stable-Update (Vorbereitung, NICHT freigegeben)

**Freigabe des Inhabers: noch nicht erteilt.** Diese Datei beschreibt die Schritte; nichts davon wurde ausgeführt.
Es gilt das bewährte Verfahren von 3.1.0/3.1.1 (`docs/release/3.1.0/VEROEFFENTLICHUNG.md`): bestehender Release
Manager (Admin-Panel, Schema 2), bestehender Offline-Schlüssel `d6139235b220a4aa`. Kein neuer Schlüssel, kein privater
Schlüssel in GitHub, CI, Chat, Logs oder auf dem VPS.

**Nicht verwenden:** den Job „Publish to the public release repository“ und keinen Tag `v3.2.0` pushen (Schema 1).

## Ausgangslage (Produktion, nur gelesen am 28.10.2026, Lauf 40765356305)

| | Wert |
|---|---|
| live Stable | **3.1.1**, Sequenz **3**, Schlüssel `d6139235b220a4aa`, gültig bis **2026-11-25T20:03:27Z** |
| Installer 3.1.1 | `138e8958754755b8dd3611f07e864d371bb3f4aea2ecae8114b33d57f885f9c1`, 157 947 314 Byte |
| Portable 3.1.1 | `bdcf3ae5b38e3393f135245e2b42c2b29ccd441022dba22222ce007715617b66`, 223 594 634 Byte |
| 3.0.0 / 3.1.0 | unverändert (Sequenz 1 / 2) |

## Release-Dateien 3.2.0

Lauf **[40775941347](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40775941347)** auf Commit `5cc77fd9eb12ed78a6d0c0ad6aacd9e34713fb71`: alle Jobs grün,
einschließlich „Windows update end to end“ (58/58) mit den Upgrades von 3.0.0, 3.1.0 und 3.1.1 (je 57/57).
Artefakt `AKENO-Gaming-Suite-windows-x64` (ID 12857297814, verfügbar bis 26.01.2027).

| Datei | Größe | SHA-256 |
|---|---|---|
| `AKENO-Gaming-Suite-3.2.0-Setup-x64.exe` | 158 176 247 B | `c9bc9d56900b17d32f6985a01a92f25611a2c2f4a24d7a0d81c3c86fbd6a8cff` |
| `AKENO-Gaming-Suite-3.2.0-portable-x64.zip` | 223 837 453 B | `8c993ae4682b308df2ffa4675c7c8a7a6d13ae8b516d576021c9a70606a95b3f` |

Alle Werte: `release-manager-values.json` in diesem Ordner. Jeder neue CI-Lauf erzeugt andere Bytes: nur die
Dateien dieses einen Laufs signieren.

## Schritte

1. Artefakt `AKENO-Gaming-Suite-windows-x64` des genannten Laufs herunterladen; `SHA256SUMS.txt` vergleichen.
2. `python tools/release_v2.py describe AKENO-Gaming-Suite-3.2.0-portable-x64.zip AKENO-Gaming-Suite-3.2.0-Setup-x64.exe`
   → Werte = `release-manager-values.json`.
3. Admin-Panel → **neues** Release (3.1.1 nicht bearbeiten):

   | Feld | Wert |
   |---|---|
   | Version | `3.2.0` |
   | Kanal / Plattform | `stable` / `windows-x64` |
   | Installer | `…-3.2.0-Setup-x64.exe` → `https://akeno.online/downloads/3.2.0/AKENO-Setup.exe` |
   | Portable | `…-3.2.0-portable-x64.zip` → `https://akeno.online/downloads/3.2.0/AKENO-Portable.zip` |
   | `install_mode` | `portable-directory-swap` |
   | `minimum_supported_version` | leer (null) |
   | `unattended_install_allowed` | nein |
   | Versionshinweise | `docs/release/3.2.0/release-notes.txt` |
   | Sequenz | **4** (größer als die live 3) |
   | Gültigkeit | höchstens 30 Tage |

4. `signed_payload` unverändert als `signed_payload-3.2.0.txt` speichern, **offline** mit `d6139235b220a4aa`
   signieren, Signatur im Admin-Panel hinterlegen. Noch nicht veröffentlichen.
5. Offline prüfen (ohne Schlüssel):

   ```
   python tools/release_v2.py verify --payload signed_payload-3.2.0.txt --signature <Signaturwert> ^
       --expect docs/release/3.2.0/release-manager-values.json --installed 3.1.1 --previous-sequence 3
   ```

   zusätzlich mit den echten Dateien (`--zip … --exe …`). Nur bei ausschließlich `[OK]` weiter.
6. Veröffentlichen (erst nach deiner Freigabe) → `production-check.yml` ausführen; erwartet: 3.2.0 live (Sequenz 4),
   3.0.0/3.1.0/3.1.1 unverändert, Downloads signaturgleich, installierte 3.0.0/3.1.0 bekommen 3.2.0 angeboten.
7. Auf einem eigenen PC mit 3.1.1: KONTO → Updates → 3.2.0 → „Jetzt installieren“; danach Lizenz, Einstellungen,
   Desktop, Spiele, Mods vorhanden; „Zurück zu 3.1.1“ sichtbar; LAN Play/VPN weiterhin aus.

## Zurücknehmen

- Vor Schritt 6: Entwurf verwerfen; 3.1.1 bleibt live.
- Nach Schritt 6: Korrektur nur über eine höhere Version (3.2.1, Sequenz 5); auf einem PC: KONTO → Updates →
  „Zurück zu 3.1.1“.

## Gültigkeit

3.1.1 läuft am 25.11.2026 ab. Wird 3.2.0 bis dahin nicht veröffentlicht, 3.1.1 vorher mit neuer Sequenz neu
ausstellen (dann bekommt 3.2.0 die nächsthöhere Sequenz).
