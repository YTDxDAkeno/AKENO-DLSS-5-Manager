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
3. **DNS-Leckprüfung (VPN):** Packet-Monitor-Datensätze von Layer-3-Adaptern werden korrekt gelesen (nur mit
   gültigem IP-Header); Antwortzuordnung über Resolver, Ports und Transaktion; Packet Monitor wird erst nach
   bestätigter Aufzeichnung benutzt; zweite Positivkontrolle nach den Sperrproben; stille Verwerfungen werden über das
   **WFP-Classify-Drop-Ereignisprotokoll** (`akeno/network/wfp_events.py`, nur lesend) für genau das Proben-Tupel
   nachgewiesen; eine auf einer Leitung beobachtete Probe gilt als Leck. Ist die Ereignissammlung aus → fail closed.
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
| VPN/WireGuardNT inkl. DNS-Leckprüfung | ausstehend | — | ausstehend (siehe Abschnitt 6, D1) |
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
| Client (Python 3.12, Offscreen-Qt) | ausstehend |

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
- **D1 DNS-Leck-Nachweis (VPN, Windows):** ausstehend bis zum grünen `wireguard`-Job. Solange er rot ist, darf das
  verwaltete VPN nicht als „Verbunden“ freigegeben werden — die App bricht dann ehrlich mit
  „Prüfung unvollständig“ ab (fail closed). Da ohnehin kein Ausgangsserver existiert, blockiert D1 die Auslieferung
  von 3.2.0 an Freunde nicht, wohl aber jede spätere VPN-Aktivierung.

## 7. Installations- und Updateanleitung (ausstehend: Artefakt-Links)

## 8. Offline-Signierung und Freigabeschritte (ausstehend)

## 9. Checkliste für den Eigentümer (ausstehend)
