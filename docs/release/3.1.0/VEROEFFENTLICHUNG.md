# AKENO Gaming Suite 3.1.0 – Veröffentlichung als Stable-Update

Diese Schritte schalten 3.1.0 für alle Nutzer frei. **Erst nach ausdrücklicher Freigabe ausführen.** Sie nutzen
ausschließlich den bestehenden Release Manager (Admin-Panel, Release-Schema 2 unter
`https://api.akeno.online/api/v1/releases/*`) und den bestehenden Offline-Signaturweg mit dem Schlüssel
`d6139235b220a4aa`. Es wird kein Schlüssel erzeugt, kein privater Schlüssel kommt in GitHub, CI oder Logs.

**Nicht verwenden:** den Job „Publish to the public release repository“ (startet bei `v*`-Tags oder per
`workflow_dispatch` mit `publish_channel`). Er schreibt die älteren GitHub-Kanalmanifeste (Schema 1). Deshalb wird
für 3.1.0 **kein Git-Tag `v3.1.0` gepusht**.

## Ausgangslage (Produktion, nur gelesen)

| | Wert |
|---|---|
| veröffentlicht | Stable **3.0.0**, Sequenz **1**, Schlüssel `d6139235b220a4aa` |
| ausgestellt / gültig bis | 2026-10-26T02:26:15Z / 2026-11-25T02:26:15Z |
| Installer 3.0.0 | `fe919b720e7db4a7e3e8fce97555bb77cde23851d205de3f039d847b1e0dd20f`, 157 148 888 Byte |
| Portable 3.0.0 | `d4c5701988f9f21841e1bd8a2c059c32e02b6e1de3f50180849837b7eb95e9f9`, 222 769 069 Byte |

Die 3.0.0-Dateien und ihr Release bleiben unverändert (`/downloads/3.0.0/…`). 3.1.0 bekommt ein **neues** Release.

## Schritte

1. **Dateien holen.** GitHub Actions → „Build and test“ → der Lauf des Release-Commits (siehe Release-Bericht) →
   Artefakt `AKENO-Gaming-Suite-windows-x64`. Nur einen Lauf verwenden, in dem alle Jobs grün sind, auch
   „Windows update end to end“ mit dem Schritt „Upgrade path from the published stable release“.
   Inhalt: `AKENO-Gaming-Suite-3.1.0-Setup-x64.exe`, `AKENO-Gaming-Suite-3.1.0-portable-x64.zip`,
   `AKENO-Gaming-Suite-3.1.0-source.zip`, `AKENO-community-dependencies-3.1.0-x64.zip`, `SHA256SUMS.txt`.
2. **Prüfsummen vergleichen:** `SHA256SUMS.txt` gegen den Release-Bericht. Jeder neue CI-Lauf erzeugt andere
   Bytes; nur die Dateien genau dieses Laufs signieren.
3. **Werte für den Release Manager:**
   `python tools/release_v2.py describe AKENO-Gaming-Suite-3.1.0-portable-x64.zip AKENO-Gaming-Suite-3.1.0-Setup-x64.exe`
   Die Werte müssen dem Release-Bericht entsprechen (`file_count`, `uncompressed_size_bytes`, `content_sha256`,
   Größen, SHA-256).
4. **Admin-Panel:** neues Release **3.1.0**, Kanal `stable`, Plattform `windows-x64`. Setup-EXE und Portable-ZIP
   hochladen (Ziel `https://akeno.online/downloads/3.1.0/AKENO-Setup.exe` und `…/AKENO-Portable.zip`).
   `install_mode` = `portable-directory-swap` (mit Portable-Paket), `minimum_supported_version` = leer oder
   ≤ 3.0.0, `unattended_install_allowed` = nein. Versionshinweise: `docs/release/3.1.0/release-notes.txt`.
   Die Sequenz muss **größer als 1** sein (3.0.0-Clients lehnen eine gleiche Sequenz mit anderem Inhalt ab).
5. **Signaturanfrage** im Admin-Panel erzeugen und **offline** mit dem bestehenden Schlüssel signieren (privater
   Schlüssel nur im eigenen Tresor). Signatur im Admin-Panel hinterlegen. Noch **nicht** veröffentlichen.
6. **Vorab prüfen** mit der Vorschau-JSON des Admin-Panels (genau die Antwort, die `GET /api/v1/releases/stable`
   liefern wird):
   `python tools/release_v2.py verify release-3.1.0.json --zip …portable-x64.zip --exe …Setup-x64.exe --installed 3.0.0 --previous-sequence 1`
   Erwartet: `[OK] signature valid (key d6139235b220a4aa)`, `[OK] sequence … after the published 1`,
   `[OK] AKENO 3.0.0 can install it itself`, `[OK]` für Installer, Portable, `file_count`,
   `uncompressed_size_bytes`, `content_sha256`.
7. **Freigabe einholen**, dann im Admin-Panel veröffentlichen.
8. **Nachprüfung:** GitHub Actions → „Production read-only check“ → „Run workflow“. Erwartet:
   `[OK] stable 3.1.0 verified (key d6139235b220a4aa)` für Portable und Installer, `install mode
   portable-directory-swap`, `AKENO 3.0.0 installs it after confirmation`.
9. **Auf einem eigenen 3.0.0-PC:** KONTO → Updates → „Nach Updates suchen“ → 3.1.0 → herunterladen → „Jetzt
   installieren“. Danach: Lizenz aktiv, Einstellungen, Spiele, installierte Mods und Backups vorhanden; unter
   KONTO → Updates steht „Zurück zu 3.0.0“.

## Zurücknehmen

- Vor Schritt 7: Entwurf im Admin-Panel verwerfen; nichts erreicht Nutzer.
- Nach Schritt 7: Ein „Restore as Latest“ auf 3.0.0 hilft Clients, die 3.1.0 gesehen haben, nicht (Anti-Replay,
  Version nie kleiner). Korrektur nur über einen höheren Build (3.1.1). Auf einem PC selbst: KONTO → Updates →
  „Zurück zu 3.0.0“ (die vorherige Version wird nach jedem Update aufbewahrt).

## Hinweis Gültigkeit

Signierte Release-Informationen gelten höchstens 30 Tage. Vor Ablauf von 3.1.0 im Admin-Panel neu ausstellen
(neue Sequenz), sonst bieten Clients kein Update an.
