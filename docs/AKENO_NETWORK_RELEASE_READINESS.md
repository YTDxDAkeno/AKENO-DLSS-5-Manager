# AKENO Gaming Suite 3.2.0 — AKENO Network release readiness

Stand: 28.10.2026. Ehrlicher Stand eines **Release-Kandidaten**. Nichts wurde veröffentlicht, gemergt, getaggt,
deployt oder an Schlüsseln/Infrastruktur verändert. Freigaben bleiben beim Eigentümer.

## 1. Branch und Commit

- Branch: **`claude/adoring-cerf-pzowiw`** (aufgesetzt auf `claude/inspiring-gauss-i8vjdr` @ `bf4c618`).
- Neuester Commit: siehe `git ls-remote origin refs/heads/claude/adoring-cerf-pzowiw` (die Commit-Liste mit
  Beschreibung steht in `docs/AKENO_NETWORK_RELEASE_PROGRESS.md`).
- Aus `codex/akeno-network-staging-20261028` (`325d35c`) wurde ausschließlich `deploy/network-staging/` übernommen
  (Commit `40f243d`); der App-Code dort ist identisch mit `588b21c`.

## 2. Implementierte Features (diese Arbeit)

| Bereich | Umsetzung |
|---|---|
| „Not Found“-Fix | `LanClient` übersetzt 404/405 der LAN-Routen in `LanUnavailable`; `akeno/network/messages.py` macht aus jedem LAN-Fehler einen DE/EN-Satz; `Controller.run` zeigt in der Statusleiste nie mehr rohe Serverantworten; neue Server-Route `GET /api/v1/network/lan/status` (nur wenn aktiviert) |
| Premium-Redesign | `akeno/ui/pages/akeno_network.py` + `akeno/ui/network_widgets.py`: Verbindungs-Dashboard (LAN/VPN), animierter Status-Orb (nur beim Zustandswechsel), Tabs LAN/VPN/Diagnose, Schritt-Anzeige 1–4, (i)-Info-Buttons und „So funktioniert…“-Bereiche statt Fließtext, Banner statt Statuszeile, Avatare, Qualitätsbalken aus echter Ping-Messung, gemalte Flaggen (Windows hat keine Farb-Flaggen-Emojis), nur Tokens aus `theme.py` |
| LAN Play | Raumname, optionales Passwort, Einladung anzeigen/kopieren/erneuern (nur Besitzer), Anleitung für Freunde nach dem Erstellen, Besitzer-Badge, Anzeigenamen der Mitglieder (Server liefert nur `display_name`, nie E-Mail), Mitglied entfernen, Verlassen/Löschen je nach Rolle, virtuelle IP kopieren, Pfad Direkt/Relay, Ping, Qualität, Wiederherstellen; Server-Verfügbarkeit wird einmal read-only geprüft und Erstellen/Beitreten bei fehlendem Dienst gesperrt |
| Module | `NetworkModules`: LAN Play und VPN getrennt ein-/ausschaltbar, **standardmäßig aus**, Updates schalten nie ein; Einstellungen → Komponenten zeigt beide |
| VPN | Provider-Architektur `akeno/network/providers.py` (nur geprüfte Provider, unbekannte deaktiviert), Standortliste mit Suche, Favoriten, Länder-Tag für eigene Profile, großer Verbinden/Trennen-Button, Exit-IP, Verlauf (nur Zeit/Ort/Dauer), echte Nichtverfügbarkeitsgründe für TR/US/DE/NL/GB/FR/JP/CA; VPN Gate deaktiviert |
| Diagnose | zusätzlich DNS, AKENO-Server, LAN-Dienst, Koordinator/Relay, Verbindungsweg, VPN-Routing; Export als Text mit Redaktion (keine Tokens/Passwörter/Schlüssel) |
| LAN-Discovery | Kern für raumbezogene Broadcast/Multicast-Weiterleitung (`akeno/network/discovery.py`): nur Spielports, keine Windows-Dienstports/SSDP/mDNS/LLMNR, nur Ziele im Raum-/24, Größen- und Ratenlimits; **Windows-Erfassung nicht implementiert → `AVAILABLE = False`** |
| Server | Heartbeat liefert Raumname/Besitzer, Mitglieder mit `owner` und `name` (rückwärtskompatibel) |
| Version | **3.2.0** (veröffentlicht: 3.0.0 Seq. 1, 3.1.0 Seq. 2; 3.1.1 vorbereitet) — keine Nummer wird wiederverwendet |
| Doku | `AKENO_VPN_PROVIDERS.md`, `AKENO_LAN_COMPATIBILITY.md`, `RELEASE_NOTES.md` (3.2.0), dieser Bericht |

## 3. Tatsächlich getestet

Lokal (Linux, Python 3.13, offscreen Qt) in dieser Arbeit:

- Server: **175 passed** (inkl. neuer Tests: Status-Route fehlt ohne Aktivierung, Status mit Aktivierung, Heartbeat-Raumdaten, echter Desktop-Client gegen echten uvicorn-Server **ohne** LAN-API → `LanUnavailable` statt 404).
- Client-Netzwerk/UI: `test_network_ui.py` (inkl. „Not Found“-Regression, Modul-Aus, Dienst-Aus, Besitzer/Mitglied-Ansicht, VPN-Standorte, Dashboard aus echtem Verbindungsergebnis, redigierter Export, Orb-Animation), `test_network_providers.py` (6), `test_network_modules.py` (3), `test_network_discovery.py` (13, inkl. echter UDP-Zustellung und Unicast-Antwort), i18n-Abdeckung.
- Vollständige Client-Regression: **1326 passed, 19 skipped, 0 failed** (lokal, 24 min).

Nicht in dieser Arbeit ausgeführt (bleiben aus früheren CI-Läufen belegt, siehe `AKENO_NETWORK_PROGRESS.md`):
WireGuardNT auf Windows (40 Checks), LAN-Session auf Windows (26 Checks), NAT-Lab 37/37, OpenTTD über P2P/Relay,
Update-E2E 58/0, Upgrades 3.0.0/3.1.0 57/0, Repair 15/0 — alle auf Commit `588b21c`.

## 4. Windows-CI

**`build.yml` Lauf 310 (https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40630526117), Commit
`edc02c6`, vollständig grün:** Linux-Tests, Lizenzserver (SQLite, PostgreSQL 16, Docker-Deployment), Upstream-
Paketverträge, Windows-Unit/Integration/UI-Tests, Smoke-Test, PyInstaller, gepackte App startet, Desktop Studio,
Portable-ZIP, **Inno-Installer 3.2.0**, Release-Manager-Werte, Prüfsummen; **Update-E2E 58/0**, **Upgrade vom
veröffentlichten 3.0.0-Installer 57/0**, **vom 3.1.0-Installer 57/0**, **Repair 15/0**. Zusätzlich auf `f2f5d84`
(gleicher Netzwerk-Code): `network-windows.yml` Lauf 23 (WireGuard/LAN-Session auf Windows) und `lan-lab.yml`
Lauf 13 (NAT-Lab) grün. Lokale Regression: Client 1326 passed / 19 skipped / 0 failed, Server 175 passed.

## 5. Release-Kandidaten-Paket

Das Artefakt `AKENO-Gaming-Suite-windows-x64` des grünen `build.yml`-Laufs (Installer
`AKENO-Gaming-Suite-3.2.0-Setup-x64.exe`, Portable-ZIP für den Updater, `SHA256SUMS.txt`, Release-Manager-Werte).
Die Signatur des Release-Envelopes (Ed25519, Schema 2, Sequenz 3) erfolgt **offline** beim Eigentümer; der private
Schlüssel kommt nie auf VPS oder GitHub.

## 6. Screenshots

`screenshots/network-fixtures/` (EN/DE): leer, aktive Sitzung, VPN-Tab, Diagnose — **synthetische Fixture-Daten**,
gerendert im echten Hauptfenster unter Linux. Keine Screenshots einer echten Verbindung.

## 7. Modulare Installation

- Installer (`packaging/installer.iss`): Typen „Vollständig“, „Core (ohne Security Center)“, „Benutzerdefiniert“.
  **Security Center ist abwählbar** und wird dann physisch nicht installiert bzw. bei Upgrade entfernt; Updates
  projizieren das signierte Paket auf diese Auswahl (`selected_content`), installieren es also nicht heimlich nach.
- Später verwalten: Einstellungen → Komponenten (Security Center installieren/reparieren/entfernen/aktivieren;
  LAN Play und VPN ein/aus).
- LAN Play/VPN sind Teil von Core (wenige Python-Module, kein eigener Treiber/Dienst). Die Netzwerkkomponente ist das
  **offizielle, signierte WireGuard für Windows**, das AKENO nicht installiert und vor Nutzung per Authenticode
  prüft. Ein physisch getrenntes Netzwerk-Paket wäre ohne Treiber kein Sicherheits- oder Größengewinn; es ist
  bewusst nicht umgesetzt.
- Norton: Ohne Erkennungsnamen/Quarantäneprotokoll keine Analyse möglich. Core-Installation ohne Security Center ist
  der vorhandene Weg; wird `AKENO.exe`/der Installer selbst erkannt, hilft nur Code-Signing + Meldung an Norton
  (False-Positive-Formular). Keine Umgehung.

## 8. LAN Play — wie es funktioniert

Raum (Server: Mitgliedschaft, Einladung 10 min, Bann) → jedes Mitglied hat den Koordinator als WireGuard-Peer
(Schlüsselbesitz per Noise-Handshake, NAT-Endpunkt) → Heartbeat liefert pro Mitglied ein signiertes ≤45-s-Ticket
→ `PathController`: zuerst direkt (P2P), nach 12 s Relay über denselben UDP-Port, erneute Versuche → Windows:
ein erhöhter Helper pro Sitzung (UAC einmal), eigener Adapter, Firewall nur für den Raum (SMB/RDP/NetBIOS gesperrt),
Spiele einzeln freigeben. Normaler Internetverkehr bleibt unverändert.

## 9. Wirklich verfügbare VPN-Standorte

**Nur eigene, vom Nutzer importierte WireGuard-Profile.** Kein AKENO-VPN-Server läuft; TR, US, DE, NL, GB, FR, JP,
CA werden mit dem Grund „In diesem Land läuft noch kein AKENO-VPN-Server“ angezeigt. VPN Gate deaktiviert.
Optionen und Kosten: `AKENO_VPN_PROVIDERS.md`.

## 10. „Not Found“ — Ursache und Behebung

- **Ursache:** Der produktive Lizenzserver läuft ohne `AKENO_NETWORK_ENABLED`; der Router `/api/v1/network/lan/*`
  existiert dort nicht. Jede LAN-Aktion (Raum erstellen/beitreten/aktualisieren) erhielt Starlettes 404 mit
  `{"error":{"code":"not_found","message":"Not Found"}}` → `ApiError("Not Found")`. `Controller.run` schrieb
  `str(exc)` 8 s lang in die Statusleiste (**unten links**), die Seite wiederholte den Text. Es lag also an der
  echten Anwendung gegen den echten Server, nicht an der synthetischen Vorschau (die Fixture-Skripte rufen keine
  LAN-Aktion auf). Ein Live-Abruf von `api.akeno.online` war aus dieser Umgebung durch den Proxy blockiert; die
  Ursache ist durch Code (`network_enabled=False` Standard, `test_routes_absent_by_default`) und den neuen
  Ende-zu-Ende-Test belegt.
- **Behebung:** siehe Abschnitt 2, Zeile 1. Zusätzlich sperrt die Seite Erstellen/Beitreten mit Erklärung, wenn der
  Server LAN Play nicht anbietet.
- **Regressionstests:** `test_server_without_lan_api_never_shows_not_found`,
  `test_controller_status_bar_translates_server_answers`, `test_room_errors_become_sentences`,
  `test_client_against_server_without_lan_api_reports_unavailable_not_404`, `test_routes_absent_by_default`.

## 11. Offene Release-Blocker

1. ~~Grüner `build.yml`-Lauf~~ — erledigt (Lauf 310 auf `edc02c6`). Bei weiteren Commits erneut prüfen.
2. **LAN-Dienst in Produktion nicht aktiviert** (bewusst): erfordert gesonderte Sicherheitsprüfung, Kapazitäts- und
   Missbrauchslimits, Monitoring, öffentliche UDP-Freigabe. Bis dahin zeigt 3.2.0 „noch nicht aktiv“ — das ist ein
   sicherer, ehrlicher Zustand und kein Blocker für 3.2.0 selbst.
3. **Zwei echte Windows-PCs hinter verschiedenen ISP-Routern** — nicht getestet (Plan: `AKENO_NETWORK_PRIVATE_TEST_RELEASE.md`).
4. **UAC-Start des Helpers aus dem installierten GUI** — nicht getestet.
5. **Broadcast/Multicast-Discovery unter Windows** — nicht implementiert (`AVAILABLE = False`, UI sagt es).
6. **VPN:** keine AKENO-Server, kein Kill Switch, Standby/WLAN-Wechsel ungetestet.
7. **Norton:** kein Erkennungsname geliefert; Code-Signing fehlt.
8. Physische Mehr-PC-Tests der neuen Oberfläche bei 125/150 % DPI unter Windows (CI erstellt 150-%-Screenshots der
   übrigen Seiten; die Netzwerkseite wurde nur unter Linux gerendert).

## 12. Freigabecheckliste (Eigentümer)

- [ ] `build.yml` grün auf dem finalen Commit; Artefakt geladen; `SHA256SUMS.txt` geprüft.
- [ ] Installer auf sauberem Windows: Typ „Core“ (ohne Security Center) und „Vollständig“; Start, Login, Lizenz.
- [ ] Update 3.0.0 / 3.1.0 / 3.1.1 → 3.2.0 über KONTO → Updates; Einstellungen, Desktop, Komponenten unverändert;
      LAN Play und VPN danach **aus**; kein WireGuard-Dienst/Adapter neu.
- [ ] Rollback über KONTO → Updates geprüft.
- [ ] AKENO Network: Seite zeigt „noch nicht aktiv“, keine „Not Found“-Meldung; Diagnose + Export ohne Geheimnisse.
- [ ] Eigenes WireGuard-Profil importieren, verbinden, Exit-IP, trennen; Routen/DNS wiederhergestellt.
- [ ] Deutsch/Englisch durchklicken.
- [ ] Release-Envelope 3.2.0 (Schema 2, Sequenz > aktuelle) **offline** signieren und über die Release-API
      veröffentlichen; Webseite zeigt Version aus der API (keine harte Versionsnummer); Installer primär, ZIP sekundär.
- [ ] Erst danach (gesonderte Freigabe): LAN-Staging öffentlich → Zwei-PC-Test → Produktions-LAN-Dienst.
