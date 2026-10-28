# AKENO Gaming Suite 3.2.0 — finaler Releasebericht (Release-Kandidat, nicht veröffentlicht)

Stand: 28.10.2026, Claude-Fortsetzung. **Nichts wurde veröffentlicht, signiert, gemergt, getaggt oder deployt.**
Produktions-VPS, DNS, Caddy, Firewall, Lizenz- und Release-Schlüssel sowie kostenpflichtige Ressourcen wurden nicht
angefasst. Der laufende Arbeitsstand steht in `docs/AKENO_320_FINAL_PROGRESS.md`.

> Dieser Bericht wird mit dem letzten Commit dieser Arbeit abgeschlossen. Abschnitte mit **„ausstehend“** sind zum
> Zeitpunkt des Schreibens noch nicht belegt und dürfen nicht als erledigt gelesen werden.

## 1. Quelle

| | |
|---|---|
| Entwicklungsbranch | `claude/confident-goodall-i10aop` (Fast-Forward vom initialen `main`, kein Force-Push) |
| Ausgangsbasis | `codex/akeno-network-cloud-20261028` @ `1991686c9c4f4f3d975de35b7da9b8dd26543c54` (enthält vollständig `claude/adoring-cerf-pzowiw` @ `9eead86`, `codex/akeno-network-plug-and-play` @ `4190496`, `feature/akeno-network-modular`, `hotfix/akeno-3.1.1-doctor-startup`) |
| Staging | `codex/akeno-network-staging-20261028` @ `325d35c` zusammengeführt (`a8ca408`; Dateien waren bereits identisch) |
| Getesteter Commit | siehe Abschnitt 4 (CI auf genau einem Commit) |
| Version | **3.2.0** — unveröffentlicht. Live-Stable laut Produktions-Lesecheck: **3.1.1, Sequenz 3** → 3.2.0 bekommt **Sequenz 4** |

## 2. Changelog dieser Fortsetzung (gegenüber `1991686`)

1. **Echte Relay-only-Transportsperre (Sicherheitsfix).** CI bewies einen realen Bypass: WireGuardNT nahm
   authentifizierte direkte Datagramme an und wechselte den Peer vom Relay auf die Angreiferadresse (WireGuard-Log:
   Peer „created“ mit `172.29.77.1:64610`, „destroyed“ mit `172.29.77.14:54937`). Die Defender-Regeln am
   Listen-Port greifen beim Kernel-Socket nicht. Neu: `akeno/network/wfp.py` — AKENO-eigener, persistenter WFP-Provider
   und -Sublayer, sechs Filter pro LAN-Tunnel auf `INBOUND/OUTBOUND_TRANSPORT_V4/V6` (nur UDP Port P ↔ Relay erlaubt,
   alles andere auf P gesperrt, IPv6 komplett). Installation/Austausch/Entfernen atomar in einer BFE-Transaktion,
   Prüfung vor dem Tunnelstart und bei jedem Heartbeat (fehlt sie → Sitzung endet, nie „Verbunden“), bleibt nach
   Absturz bis zur Wiederherstellung, wird erst nach dem Tunnel entfernt; nur AKENO-eigene Objekte werden gelöscht.
2. **LAN-Firewall:** neue Default-Deny-Sätze entfernen alle älteren Sätze desselben Tunnels (Kollision gefunden in CI).
3. **DNS-Leckprüfung (VPN):** Jede fremde DNS-Probe (UDP/TCP 53, TCP 853, über jede physische Quelle) braucht
   einen eigenen Kernel-Nachweis: explizite Zugriffsverweigerung, Packet-Monitor-Drop-Datensatz oder ein Eintrag im
   **WFP-Classify-Drop-Ereignisprotokoll** (`akeno/network/wfp_events.py`, nur lesend) für genau das Proben-Tupel.
   Eine auf einer Leitung beobachtete Probe gilt als Leck; fehlt ein Nachweis oder ist die Ereignissammlung aus →
   fail closed. Packet Monitor sieht den WireGuardNT-Adapter nur stichprobenartig (CI: in einem Lauf aufgezeichnet,
   im nächsten nicht) — „nicht gesehen“ ist deshalb nie ein Beweis; ob die Aufzeichnung den Tunnel erfasst hat, wird
   gemeldet (`capture_recorded_tunnel`), nicht vorausgesetzt. Layer-3-Datensätze werden nur mit gültigem IP-Header
   gelesen.
4. **Linux-Segfault (Exit 139) behoben:** Fortschritts-Relay der System-Seiten lebt jetzt prozessweit; geschlossene
   Seiten brechen ihre Messung ab; Tests warten nach jedem Test auf alle Hintergrundaufgaben.
5. **Windows-Tests:** msiexec-Pfadvergleich nativ; zu lange Test-ID (Windows-Umgebungsvariablenlimit) gekürzt —
   reine Testannahmen, keine Abschwächung.
6. **Ein-Klick-VPN-Oberfläche:** Länderkacheln mit Flagge und Grund, Verbinden/Trennen, verifizierte IP/IPv6/Land,
   Verbindungsdauer, Leckschutz, Latenz nur wenn gemessen, veraltete Status → „Wiederherstellung nötig“, übersetzte
   Fehlercodes; eigene WireGuard-Profile im Expertenbereich. Sitzung meldet `connected_since`.
7. **LAN-Oberfläche:** IP-Schutz-Zeile („Geschützt: nur über Relay“), ohne verifizierte Sperre nie „Verbunden“;
   Serverbrowser mit „Alle Spiele/Regionen“, Spiel-/Regionsnamen, übersetztem Status und Meldegründen.
8. **CI:** Netzwerklabore auch auf `claude/**`; gebautes `AKENO.exe --network-helper` wird über das runas-Verb
   geprüft; Upgrade-Pfad von der Live-Version **3.1.1** ergänzt.
9. **Doku:** Release-Notes 3.2.0 (EN in `docs/RELEASE_NOTES.md`, DE/EN in `docs/release/3.2.0/release-notes.txt`),
   Fixture-Screenshots neu (inkl. Fixture-VPN-Verbindung mit Dokumentations-IPs).

## 3. Echte Windows-Testergebnisse

| Prüfung | Lauf | Commit | Ergebnis |
|---|---|---|---|
| LAN Play auf echtem Windows (WireGuardNT, isolierter Windows-Peer) | [40762936142](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40762936142) | `482ffa4` | **44/44 grün**, inkl. verschlüsseltem Direct-Roaming-Angriff (blockiert), Crash/Recovery mit Sperre |
| VPN/WireGuardNT inkl. produktiver DNS-Leckprüfung | [40766590526](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40766590526) | `118e7e0` | **41/41 grün**: `dns_verified`, 3 gesperrte + 3 nicht routbare Fremd-DNS-Pfade, IPv4/IPv6 nur durch den Tunnel, Routen/DNS danach identisch |
| Komponenten-Bootstrap (Authenticode, geschützter Installer) | in jedem `network-windows`-Lauf | — | 11/11 grün (Schritt „explicit-consent official component bootstrap“) |
| Vollständiger Build, Installer, Portable, Update-E2E, Rollback, Upgrades 3.0.0/3.1.0/3.1.1 | ausstehend | — | ausstehend |
| Gebauter Helper über runas | ausstehend | — | ausstehend |

## 4. CI auf dem finalen Commit

ausstehend — wird mit Lauf-Links für `build.yml`, `network-windows.yml`, `lan-lab.yml`, `network-local-e2e.yml`,
`components-windows.yml` auf genau einem Commit eingetragen.

## 5. Lokale Regression

| Suite | Ergebnis |
|---|---|
| Server (`server/`, Python 3.12) | **262 passed** |
| Client (Python 3.12, Offscreen-Qt, Linux), vollständig | **1536 passed, 19 skipped, 0 failed** (19:35 min). Übersprungen nur plattform-/assetgebundene Tests (Windows-PowerShell/DPAPI/DACL/Authenticode/NTFS, Original-Release-Assets) — diese laufen in den Windows- und Real-Assets-CI-Jobs |
| Netzwerk-Testmodule nach der DNS-Änderung (`d32a0cc`) | **296 passed, 2 skipped** |

## 6. Releasebewertung

### A. Fertig und real getestet
- Relay-only-LAN mit AKENO-WFP-Sperre auf echtem Windows (44 Checks), NAT-Labor, verschlüsseltes Relay-/OpenTTD-
  Labor, VPN-Exit-Labor (Linux, NAT/NAT66, Ablauf, Absturz) — siehe `docs/AKENO_320_FINAL_PROGRESS.md`.
- Lizenzserver SQLite/PostgreSQL/Docker-Deployment; Upstream-Paketverträge; Linux-Testlauf.
- Produktions-Lesecheck: 3.0.0/3.1.0 unverändert, 3.1.1 live (Sequenz 3), Downloads signaturgleich.

### B. Implementiert, aber noch nicht real getestet
- Zwei physische PCs hinter verschiedenen ISP-Routern (kein Zugriff) — Testplan Abschnitt 9.
- UAC-Dialog auf einem normalen Benutzerkonto (Runner hat kein UAC-Prompt), anderes Administratorkonto.
- WLAN-Wechsel, Standby/Resume, Neustart mit aktivem LAN-Tunnel (Logik vorhanden, nicht nativ gemessen).
- Factorio/Terraria (nur OpenTTD real im Labor).

### C. Technisch vorbereitet, externe Voraussetzung fehlt
- **Echte VPN-Ausgangsserver:** kein Server in TR/US/DE/NL/GB/FR/JP/CA. Deployment, Registrierung, Leases,
  Länderprüfung, NAT/NAT66, Widerruf sind implementiert (`docs/AKENO_VPN_SERVER_DEPLOYMENT.md`); Anbieterwahl,
  Kosten, Nutzungsrechte und Freigabe liegen beim Eigentümer. Alle Länder bleiben in der App deaktiviert mit Grund.
- **Produktive LAN-Koordinator/Relay-Aktivierung** auf dem VPS (Staging lokal erfolgreich, öffentlich nicht freigegeben).
- **Code-Signing-Zertifikat** fehlt (Norton-Thema nicht gelöst).
- **Offline-Signatur** des Release-Envelopes (Sequenz 4).

### D. Sicherheitskritische Blocker
- Keiner offen auf den getesteten Commits. Behoben in dieser Arbeit: **D1** DNS-Leck-Nachweis (grün auf `118e7e0`,
  41/41) und **D2** ungeschützter Peer-Verkehr/Direct-Roaming im LAN (grün auf `482ffa4`, 44/44). Beide Prüfungen
  müssen auf dem finalen Commit erneut grün sein (Abschnitt 4).

## 7. Installations- und Updateanleitung

**Neuinstallation (Freunde ohne AKENO):** `AKENO-Gaming-Suite-3.2.0-Setup-x64.exe` ausführen (keine
Administratorrechte nötig). Typ „Vollständig“, „Core (ohne Security Center)“ oder „Benutzerdefiniert“. Mit Norton:
„Core“ wählen; meldet Norton den Installer oder `AKENO.exe` selbst, hilft nur Code-Signing bzw. eine
False-Positive-Meldung an Norton (keine Umgehung, keine Ausschlüsse durch AKENO).

**Update bestehender Installationen (3.0.0, 3.1.0, 3.1.1):** KONTO → Updates → „Nach Updates suchen“ → 3.2.0 →
„Jetzt installieren“. Konto, Lizenz, Einstellungen, Desktop, Modulauswahl und Daten bleiben; LAN Play und VPN bleiben
aus; es wird kein Netzwerkadapter und kein WireGuard installiert. Rückweg: KONTO → Updates → „Zurück zu …“.

**AKENO Network einschalten (freiwillig):** ONLINE → AKENO Network → LAN Play bzw. VPN einschalten →
„Netzwerkkomponenten installieren oder reparieren…“ (offizielles WireGuard, Signaturprüfung, ein UAC-Dialog) →
Raum erstellen/Serverbrowser → „LAN-Sitzung starten“ (ein UAC-Dialog) → „Spiel freigeben“.

**Voraussetzung auf dem Server:** LAN Play braucht den freigeschalteten AKENO-LAN-Dienst (Koordinator/Relay). Bis
zur Freigabe zeigt die App „LAN Play ist auf dem AKENO-Server noch nicht aktiv“ und erstellt nichts.

## 8. Offline-Signierung und Freigabeschritte (vorhandene Werkzeuge)

Es gilt das Verfahren von 3.1.0 (`docs/release/3.1.0/VEROEFFENTLICHUNG.md`) mit diesen Abweichungen; die genauen
Werte des freigegebenen Laufs stehen in `docs/release/3.2.0/` (Release-Manager-Werte werden aus dem grünen Lauf
übernommen):

1. Nur Dateien eines `build.yml`-Laufs verwenden, in dem **alle** Jobs grün sind (inkl. „Windows update end to end“
   mit den Upgrades von 3.0.0, 3.1.0 **und 3.1.1**). Artefakt `AKENO-Gaming-Suite-windows-x64` herunterladen,
   `SHA256SUMS.txt` vergleichen.
2. `python tools/release_v2.py describe AKENO-Gaming-Suite-3.2.0-portable-x64.zip AKENO-Gaming-Suite-3.2.0-Setup-x64.exe`
   → Werte müssen `docs/release/3.2.0/release-manager-values.json` entsprechen.
3. Admin-Panel: **neues** Release 3.2.0, Kanal stable, `windows-x64`, `install_mode` `portable-directory-swap`,
   `minimum_supported_version` leer, `unattended_install_allowed` nein, Notizen = `docs/release/3.2.0/release-notes.txt`,
   **Sequenz 4** (Live: 3.1.1 = Sequenz 3), Gültigkeit ≤ 30 Tage.
4. `signed_payload` unverändert speichern und **offline** mit dem bestehenden Schlüssel `d6139235b220a4aa` signieren
   (privater Schlüssel bleibt im Tresor; nie GitHub, CI, VPS, Chat).
5. Vor der Freischaltung prüfen:
   `python tools/release_v2.py verify --payload signed_payload-3.2.0.txt --signature <Wert> --expect docs/release/3.2.0/release-manager-values.json --installed 3.1.1 --previous-sequence 3`
   (zusätzlich `--zip … --exe …` mit den echten Dateien). Nur bei ausschließlich `[OK]` weiter.
6. Veröffentlichen im Admin-Panel → Produktions-Lesecheck (`production-check.yml`) ausführen; erwartet: 3.2.0 live,
   3.0.0/3.1.0/3.1.1 unverändert, Downloads signaturgleich.
7. Achtung: 3.1.1 läuft am **25.11.2026** ab — 3.2.0 vorher veröffentlichen oder 3.1.1 mit neuer Sequenz neu ausstellen.

**Nicht verwenden:** den Job „Publish to the public release repository“ und keinen `v3.2.0`-Tag pushen (Schema 1).

## 9. Checkliste für den Eigentümer

### Auf deinem Windows-PC (vor der Freigabe)
- [ ] Artefakt des grünen Laufs installieren (Core und Vollständig je einmal), App startet, alle Seiten öffnen.
- [ ] 125 % und 150 % Windows-Skalierung: AKENO Network ohne abgeschnittene Elemente.
- [ ] Update von deinem 3.1.1 über KONTO → Updates (nach Signatur), Lizenz/Konto/Desktop erhalten, Rückweg testen.
- [ ] AKENO Network: Modul einschalten → Komponenten installieren (UAC **ablehnen** einmal: es darf nichts passieren;
      dann zustimmen) → Diagnose grün.
- [ ] Mit einem Freund hinter einem anderen Router (nachdem der LAN-Dienst freigegeben ist): Raum, Einladung,
      Session, OpenTTD/Factorio über virtuelle IP; prüfen, dass im Spiel nur `10.203.x.x`-Adressen sichtbar sind.
- [ ] Session per Task-Manager beenden → AKENO neu starten → „Wiederherstellen“ → kein Adapter/keine Regel übrig.
- [ ] WLAN wechseln, Standby/Resume während einer LAN-Sitzung (Erwartung: weiterverbunden oder ehrliche Fehlermeldung).

### Für Codex/Betrieb auf dem VPS (erst nach deiner ausdrücklichen Freigabe)
- [ ] LAN-Koordinator/Relay aus `deploy/network-staging/` bzw. `deploy/docker-compose.lan-play.yml` mit
      Kapazitäts-/Missbrauchslimits, Monitoring und Rollback aktivieren (`ACTIVATION_AND_ROLLBACK.md`).
- [ ] Öffentliche UDP-Freigabe nur für den Relay-Port; keine offenen UDP-Proxys; Bandbreitenbudget festlegen.
- [ ] VPN-Ausgangsserver erst nach Anbieter-/Kosten-/Rechtsfreigabe pro Land (`AKENO_VPN_SERVER_DEPLOYMENT.md`),
      unabhängige Länderprüfung, dann erscheinen Länder automatisch in der App.

### Offline-Signierung (du)
- [ ] Schritte 1–6 aus Abschnitt 8, Sequenz 4, Schlüssel `d6139235b220a4aa`, Prüfung mit `tools/release_v2.py verify`.
