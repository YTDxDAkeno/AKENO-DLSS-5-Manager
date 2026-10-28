# Claude: Windows-Konfiguration für den privaten LAN-Stagingtest

Geprüfter Code: `588b21ca64a78f13d937a89abb58d5289b34f7c5` auf `claude/inspiring-gauss-i8vjdr`. Der Code meldet Version **3.1.1**. Eine vollständige aktuelle Windows-CI-Freigabe wurde in dieser VPS-Vorbereitung nicht behauptet; die Übergabe nennt bei Lauf 255 noch fehlgeschlagene Update-E2E-Erwartungen und den anschließenden Testfix. Vor Auswahl eines Testbuilds alle benötigten Workflows auf demselben SHA prüfen.

| Einstellung | Vorgesehener Wert / Vertrag |
|---|---|
| Eingebetteter Lizenzserver | `https://lan-staging-api.akeno.online` **ohne** `/api/v1` |
| Tatsächliche HTTP-API | Client hängt `/api/v1` an; LAN-Pfad `/network/lan/...` |
| Account-Webseite | `https://lan-staging.akeno.online` |
| Coordinator-Host | `lan-staging-udp.akeno.online`, direkte A-Auflösung auf VPS, UDP 3478 |
| Lizenz-/LAN-Signaturvertrauen | Ausschließlich neue Staging-Ed25519-Lizenzkeys; noch nicht erzeugt |
| Koordinator-Schlüssel | Eigener neuer X25519/WireGuard-Schlüssel; öffentlich über authentifizierten Heartbeat |
| Release-Vertrauen | Bestehenden öffentlichen Release-Prüfschlüssel unverändert lassen; kein privater Release-Key auf Staging |
| Staging-Updates | Kein öffentliches Staging-/Stable-Release und kein eigenes unsigniertes Updatefeed angelegt |

`LanClient` nutzt denselben LicenseManager wie Anmeldung, Refresh und Geräteverwaltung. Ein unabhängiger LAN-API-Endpoint zusätzlich zu einem produktiven Lizenzserver ist aktuell **nicht** vorgesehen. Produktionskonten/-Tokens dürfen nicht in den Staging-Client übernommen werden.

Im eingefrorenen PyInstaller-Build ignoriert `akeno/licensing/config.py` Runtime-Endpoint-Overrides. Der Testbuild muss `akeno/data/licensing.json` **vor PyInstaller** mit neuen Staging-Pins erhalten. Die Standard-CI überschreibt diese Datei aus `AKENO_LICENSE_SERVER_URL`, `AKENO_WEBSITE_URL`, `AKENO_LICENSE_PUBLIC_KEYS` beziehungsweise den offiziellen Defaults. **Keine globalen Repositoryvariablen für den Test verändern.** Stattdessen einen ausdrücklich getrennten Staging-Buildpfad bzw. einen eingegrenzten Job mit den Staging-Werten einrichten; die Konfiguration darf danach nicht erneut mit Produktionswerten überschrieben werden.

Vorbereitet ist `configure-staging-client.ps1`. Es verlangt den exakten Quell-Commit und ein unabhängig übertragenes öffentliches Staging-Key-Dokument aus dem späteren CLI-Export. Es prüft die Zieladresse und verweigert Überschneidung mit den vorher im frischen Checkout enthaltenen Keys. Es wird nicht automatisch ausgeführt. Keine Trust-on-first-use-Abholung per `--fetch` als alleinige Vertrauensgrundlage: Key-ID über den authentisierten SSH-Kanal bestätigen.

Die Stage-Lizenzkey-Signatur ist JWS/EdDSA (`typ: akeno-lan-peer`, `iss: akeno-lan`, `aud: akeno-lan-peer`), **nicht** das Release-v2-`signed_payload`-Format. Peer-Ticket bindet Raum, Account, Gerät, WireGuard-Public-Key, virtuelle IP, iat/exp, Nonce und bewiesenen Endpoint. Höchstens 45 Sekunden gültig; Client kontrolliert Signatur und Gerätekongruenz.

Anmeldepfad: Account-Anmeldung → aktive Staging-Lizenz → PC über Geräte-API registrieren → Session an genau dieses Gerät binden. `authorised()` verlangt verifizierte E-Mail, laufende Lizenz und eigenes aktives Gerät, dessen ID mit `caller.session.device_id` übereinstimmt. Besitzer-/Actor-Angaben kommen nicht aus dem Request-Body. Bereitgestellte synthetische Testkonten sind gewöhnliche Benutzer ohne Adminrolle und erhalten einmalig sieben Tage Lizenz; ihre `.invalid`-Adressen werden nur im isolierten Staging vorab als verifiziert markiert. Das ist kein produktiver E-Mail-Test. Neue Geräte werden erst bei Anmeldung der tatsächlichen PCs erzeugt.

Raum-API: `POST /rooms`, `POST /join`, `GET /rooms/{id}/members?device_id=...`, `POST /rooms/{id}/heartbeat`, `POST /rooms/{id}/invite`, `DELETE /rooms/{id}/members/{user_id}?device_id=...`, `POST /rooms/{id}/leave`, `DELETE /rooms/{id}?device_id=...`, jeweils unter `/api/v1/network/lan`. Änderungen sind bearer-authentifiziert; Member-/Owner-Rechte bleiben verpflichtend.

Create/Join/Members melden im aktuellen Code weiterhin `transport: not_implemented`; maßgeblich für die tatsächlich gestartete Datenebene ist **Heartbeat** mit `transport: wireguard` und `coordinator`-Block. Nicht anhand des Create-Ergebnisses Transport deaktivieren. Der Heartbeat gibt je Peer dessen eigenes signiertes Ticket zurück. Endpoints entstehen ausschließlich aus echtem Schlüsselbesitznachweis am UDP-Koordinator, nicht aus einer vom Benutzer eingetragenen IP.

Client zuerst direkt P2P, nach 12 Sekunden Relay; Stille nach 60 Sekunden führt zum erneuten Versuch. Keepalive zum Koordinator 25 Sekunden; GUI-/Session-Heartbeat ca. 15 Sekunden. Direkte Verbindungen widerrufener Mitglieder enden spätestens beim nächsten Refresh der übrigen Clients; Relay-Widerruf wirkt unmittelbar. Die virtuelle Koordinatoradresse ist `10.203.0.1/32`, Räume nutzen `10.203.<1..254>.0/24`; Kollisionen mit realen PC-Netzen vorher prüfen.

Für den privaten Build **separaten Windows-Benutzer oder eine geeignete isolierte Testumgebung verwenden**. Standardmäßig ist `%LOCALAPPDATA%\AKENO` identisch mit dem produktiven Client; DPAPI-Identitäten, Refresh-Tokens, Update-Zustand und bestehende WireGuard-Profile dürfen nicht vermischt werden. `AKENO_HOME` ist im Code vorhanden, ein eigener Testbenutzer ist für die echten UAC-/WireGuard-Tests jedoch eindeutiger. Keine produktiven Anmeldedaten im Test verwenden.

TLS: öffentlich vertrauenswürdige Zertifikate nach späterer DNS-/Caddy-Freigabe; keine deaktivierte Zertifikatsprüfung, keine selbstsignierte Umgehung. Admin bleibt ausschließlich als verschlüsselter SSH-Tunnel zu Host-Loopback verfügbar. Offizielles WireGuard für Windows muss vom Client Authenticode-geprüft werden. Keine SmartScreen-/Firewall-/Antivirus-Abschaltung.

Der private Zwei-PC-Test muss danach mindestens prüfen: Raum/Invite, falsche/fehlende Lizenz, anderes Gerät, Endpoint-Proof, P2P, erzwungener Relay-Pfad, echtes Spiel per IP, SMB/RDP-Sperre, Widerruf, Lease-Ablauf, Internet-/DNS-Unverändertheit, Disconnect/Crash-Recovery sowie GUI→UAC. NAT-Lab, Loopback und diese VPS-Tests beweisen keinen erfolgreichen Test mit zwei physischen Windows-PCs hinter ISP-NAT.
