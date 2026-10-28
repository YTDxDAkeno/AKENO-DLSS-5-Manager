# Claude: tatsächlich laufendes lokales AKENO Network Staging

Geprüfter App-/Server-Code: `588b21ca64a78f13d937a89abb58d5289b34f7c5` (Version 3.1.1). Die Staging-Vorbereitung liegt auf `codex/akeno-network-staging-20261028`. Kein Merge, Tag, Release oder öffentlicher Test aktiviert.

## Tatsächlich erreichbar auf dem VPS

- API-Basis `http://127.0.0.1:18510`; LicenseApi hängt `/api/v1` an.
- Account-Web `http://127.0.0.1:18511`, Admin `http://127.0.0.1:18512`.
- Koordinator und Relay `127.0.0.1:3478/udp`; der authentisierte Heartbeat bewirbt exakt diesen lokalen Host/Port.
- Keine Staging-DNS-Einträge, keine öffentliche HTTPS-Route, keine öffentlichen UDP-Pakete freigegeben.

Client-Code `LicenseManager`, `LicenseApi`, `LanClient` und `verify_peer` wurden unverändert aus dem genannten Claude-Stand gegen die **laufende** Staging-API benutzt. Clientzustand und private Geräte-WireGuard-Schlüssel lagen ausschließlich im RAM der Testprozesse. DPAPI, WireGuardNT, UAC, zwei echte PCs und ISP-NAT sind dadurch nicht getestet.

## Öffentliche Testschlüssel

Staging-Lizenz-/LAN-Ticket-Ed25519:

```json
{"kid":"92c8ea977d38cd6c","public_key":"Ihst-Wo-X1v6FaLYGOQ6jLawv6Bkv4Xq1bu06dOMCV4"}
```

Staging-Koordinator-WireGuard/X25519:

```text
QuQjiPbDjClUbzw1ejLyNX1RvMR5MRb8ZiKYQ8ObhRg=
```

Beide Schlüssel sind öffentlich. Der Koordinator-Key blieb beim getesteten API-Neustart erhalten. Der Lizenzkey ist verschieden von den produktiven Lizenzkeys und nicht durch einen Produktionskey übernommen/endorsed. Der produktive öffentliche Release-Prüfschlüssel `d6139235b220a4aa` bleibt unverändert; kein privater Release-Schlüssel wird für Staging verwendet.

## Konkrete Clientkonfiguration

`claude-staging-licensing.json` ist das vollständige spätere `akeno/data/licensing.json` für einen gesonderten Staging-Build: `https://lan-staging-api.akeno.online`, Webseite `https://lan-staging.akeno.online`, ausschließlich obiger Staging-Lizenzkey. Diese HTTPS-Adressen sind **noch nicht aktiv**. Nicht in einen produktiven Build oder globale Repositoryvariablen übernehmen. Den öffentlichen Release-Key in seiner separaten Vertrauensdatei unverändert lassen.

`local-source-client.json` beschreibt die jetzt nutzbare Entwicklungsverbindung auf dem VPS. Aus Python-Quellcode kann `LicensingConfig('http://127.0.0.1:18510', {kid: public_key})` benutzt werden; die unveränderte Implementierung erlaubt HTTP nur für Loopback im Source-Modus. Ein eingefrorener PyInstaller-Build verweigert HTTP weiterhin. Keine TLS-Prüfung deaktivieren oder den Produktionsmodus umgehen.

Bei Bedarf kann später die reine HTTP-Kontroll-API über einen bestehenden SSH-Zugang lokal getunnelt werden:

```powershell
ssh -N -L 18510:127.0.0.1:18510 administrator@85.190.112.10
```

Das ist ausschließlich TCP/HTTP-Kontrollverkehr. SSH `-L` transportiert **kein UDP** und ersetzt keine spätere WireGuard-/Zwei-PC-Freigabe. Testkennwörter werden weder hier noch über GitHub bereitgestellt; sie liegen nur im rootgeschützten lokalen Staging-Secret-Speicher. Für einen echten Test sind sie später gesondert sicher zu übergeben/neu festzulegen. Eigener Windows-Testbenutzer bzw. strikt getrennter AKENO_HOME, keine produktiven Logins/Profile übernehmen.

Für den zukünftigen Windows-Testbuild `configure-staging-client.ps1` im getrennten Checkout mit genauem Commit und `staging-license-public-keys.json` verwenden. Die normale CI überschreibt Lizenzkonfiguration aus ihren Repositoryvariablen; einen eigenen Staging-Buildpfad konfigurieren und nach dessen letzten Konfigurationsschritt den eingebetteten Endpoint/Key prüfen. Keine globalen Produktionsvariablen ändern.

## Nachgewiesen und ausstehend

48 Live-Prüfungen bestanden: Anmeldung, aktive signierte Testlizenz, Gerätebindung, fremdes Gerät/Actor-Spoofing abgelehnt, Raum/Join/Invite/Owner-Regeln, signierte 45-Sekunden-Peer-Tickets, echter Schlüsselbesitznachweis per WireGuard-Noise über UDP, Replay-Abwehr, 64 bidirektionale verschlüsselte 1.200-Byte-Übertragungen über den tatsächlichen Relay, sofortiger Relay-Widerruf, Ban, Raumende. Alle Smoke-Räume gelöscht; Geräte widerrufen und Sessions beendet. Zwei normale synthetische Testaccounts mit zeitlich begrenzten Lizenzen bleiben erhalten. Kein Admin angelegt, keine echte Nachricht versandt.

Die Test-WireGuard-Peers sind reine Protokollimplementierungen, keine Windows-/Kerneladapter. Der Host-Loopback-Test beweist keinen direkten P2P-Pfad zwischen Internetanschlüssen. Lokale Endpoints sind absichtlich privat; die normale Windows-Prüfung öffentlicher P2P-Endpoints wird nicht gelockert.

Vor öffentlichem Zwei-PC-Test fehlen Eigentümerfreigabe, öffentliche /32-Tester-IPs, Testzeitraum, kontrollierte DOCKER-USER-ACL samt gemeinsamem Paketbudget, DNS/TLS und passend umgestellte Koordinator-Ankündigung. Details stehen im Smoke-Abschlussbericht und dem bestehenden Aktivierungsplan. Bis dahin ausschließlich Loopback-Betrieb.
