# AKENO Network – Abschluss des lokalen VPS-Staging-Smoke-Tests

Stand: 28.10.2026 03:57 CET. **Lokales Staging läuft und der Smoke-Test ist bestanden. Keine öffentliche Freigabe vorgenommen.**

## Ergebnis

**48/48 Live-Prüfungen erfolgreich**, darunter tatsächlicher Claude-LicenseManager/LanClient gegen die laufende API, signierte Lizenzen/Peer-Tickets, WireGuard-Schlüsselbesitznachweis über UDP und **64 Ende-zu-Ende-verschlüsselte Roundtrips mit je 1.200 Byte** über den tatsächlichen Relay. Das sind zwei lokale Test-Protokollpeers mit echten kryptografischen Handshakes und AEAD-Nutzdaten, keine Windows-Treiber oder echten Internetanschlüsse.

Die vier eigenen Staging-Container bleiben lokal erreichbar und gesund. **Alle sieben vorher vorhandenen Container** haben unveränderte Identitäten, Images, Startzeiten und Restart-Zähler; **alle 19 produktiven Datenbanktabellen** sind inhaltlich identisch mit dem Ausgangszustand. Caddy-, Produktions-Compose-, .env- und Backup-Script-Hashes sind unverändert. Produktive Webseite, Admin-Login, API-Health und API-Ready liefern weiterhin HTTP 200. Kein produktiver Benutzer, keine Lizenz und kein Release wurde geschrieben.

## Verwendeter Code und Betrieb

- Freigegebene Vorbereitung: `555092eb67e692c209a0681e38a175c43b77d531` auf `codex/akeno-network-staging-20261028`.
- Claude-Code weiterhin `588b21ca64a78f13d937a89abb58d5289b34f7c5`, Version 3.1.1. Branchstand vor Beginn erneut über GitHub aufgelöst.
- Eigenes App-Image: `sha256:53de16b05d304b34ec88706d9316f9cdf36ac2355b05bf37b72fd6fb97c3ab50`. Die sieben laufenden Netzwerk-Module stimmen byteweise mit dem geprüften Quellstand überein.
- PostgreSQL ist auf den bereits geprüften offiziellen Digest festgelegt; keine produktiven Daten oder Imageschlüssel übernommen.
- Eigener Projektname `akeno-network-staging`, Betriebsdateien `/srv/akeno-network-staging/source/deploy/network-staging`.
- Eigene PostgreSQL-Datenbank `akeno_network_staging`; eigene Raum-SQLite sowie ausschließlich neue `akeno-network-staging_*`-Volumes und Netze. Keine Mounts produktiver Daten/Schlüssel oder Mitgliedschaft in Produktionsnetzen.

## Tatsächliche lokale Adressen und Health

| Dienst | Hostadresse | Zustand |
|---|---|---|
| API | `http://127.0.0.1:18510` | gesund; `/api/v1/system/health` und `/ready` HTTP 200 |
| Account-Web | `http://127.0.0.1:18511` | gesund; Health HTTP 200 |
| Admin | `http://127.0.0.1:18512` | gesund; Health HTTP 200; kein Adminaccount angelegt |
| WireGuard-Koordinator/Relay | `127.0.0.1:3478/udp` | echte Handshakes und verschlüsselte Relaynutzdaten erfolgreich |
| PostgreSQL | ausschließlich internes Staging-Netz | gesund, keine Host-Portveröffentlichung |

Alle **Host-Portbindungen ausschließlich 127.0.0.1**. Innerhalb des eigenen Container-Netzraums lauscht der API-Prozess wie vorbereitet auf seinen Containerinterfaces. Keine öffentliche UDP-Bindung, kein IPv6-Staging-Port. Verbindung zu den TCP-Testports über die öffentliche VPS-IPv4 schlägt fehl; zusätzlich wurden Docker-Bindungen und Netz-/Volume-Zuordnungen geprüft. Ein unabhängiger externer Internet-Portscan war nicht Teil dieser Prüfung.

DNS der drei vorgeschlagenen Staging-Hosts ist weiterhin nicht auflösbar. Keine Caddy-Änderung/-Neuladung, Zertifikatsausstellung oder DNS-Aktion. Kein öffentlicher VPN-Exit, keine Host-WireGuard-Adapter, keine gekauften Server und kein aktiviertes VPN Gate.

## Öffentliche Staging-Testschlüssel

**Ed25519 – Staging-Lizenzen und kurzlebige LAN-Tickets:**

```json
{"kid":"92c8ea977d38cd6c","public_key":"Ihst-Wo-X1v6FaLYGOQ6jLawv6Bkv4Xq1bu06dOMCV4"}
```

**X25519 – WireGuard-Koordinator:**

```text
QuQjiPbDjClUbzw1ejLyNX1RvMR5MRb8ZiKYQ8ObhRg=
```

Der Ed25519-Key ist verschieden von den produktiven Lizenzkeys. Der Koordinator-Public-Key blieb beim bewussten Neustart der Staging-API erhalten; anschließende Handshakes bestanden wieder. Die privaten Schlüssel liegen nur in ihren eigenen Staging-Volumes mit Modus 0600/UID 10001. Token-/Pepper-/DB-Secrets sind 0400, zugehörige Host-Verzeichnisse 0700. Die synthetischen Testkennwörter liegen ausschließlich rootgeschützt als 0600 unter dem eigenen runtime-Verzeichnis. Keine Kennwörter als Shell-Argumente, keine privaten Schlüssel in Quellstand, Image, Ausgaben oder GitHub.

Ein Benutzer mit uneingeschränktem sudo auf dem VPS kann technisch weiterhin auf diese **Staging**-Secrets und Prozesse zugreifen. Der produktive öffentliche Release-Key **`d6139235b220a4aa`** bleibt unverändert; ein privater Release-Key wurde nicht verwendet oder zurückkopiert.

## Getestete Funktionen und Datenbereinigung

- Beide synthetischen normalen Benutzer angemeldet; echte Clientprüfung signierter, aktiver Testlizenzen und getrennte Gerätebindung erfolgreich. Falsches Passwort, fehlende Anmeldung, fremdes Gerät und gefälschte Actor-Angabe abgewiesen.
- Create/Join/Members, authentifizierter Heartbeat, Owner-/Invite-Regeln, Invite-Rotation und alter Invite getestet. Client vertraut ausschließlich dem Staging-Lizenzkey.
- Peer-Credentials binden Raum, Konto, Gerät, WireGuard-Public-Key und virtuelle IP; Signatur und maximal 45 Sekunden Gültigkeit kontrolliert. Tatsächlich beschädigte Signatur abgelehnt.
- Nicht registrierter WireGuard-Key erhält keine Antwort. Echte Noise-Handshakes beweisen beide Geräteendpoints; die API signiert diese bewiesenen Endpoints. Wiederholte Initiation kann einen Endpoint nicht übernehmen.
- Peer-Handshake und Antworten unverändert durch den tatsächlichen Relay vermittelt; 64 verschlüsselte 1.200-Byte-Nachrichten und Echos auf den beiden Endpunkten entschlüsselt und byteweise verglichen.
- Entferntes Mitglied verliert HTTP-/Heartbeat-/Join-Rechte und den Relay-Pfad; kein erneuter Schlüsselbesitznachweis möglich. Raumlöschung beendet den verbleibenden Datenpfad.
- Danach Räume gelöscht, Smoke-Geräte widerrufen und sämtliche Testsessions beendet. Abschluss: **2 gewöhnliche synthetische Benutzer, 2 befristete aktive Lizenzen, 0 aktive Testgeräte, 0 aktive Testsessions, 0 Räume**. Passwörter Argon2id-gehasht; kein Administrator und keine echte E-Mail.

Die Geräte-WireGuard-Privatschlüssel und Clientzustände waren nur im RAM des Testprozesses. Die zwei synthetischen Konten bleiben für weitere gesondert freigegebene Arbeiten erhalten; neue reale PCs haben keine durch den Smoke-Test belegten Geräteplätze. Die Lizenzen sind absichtlich auf sieben Tage begrenzt. `mail_backend=file`; produktive SMTP-Zugangsdaten wurden nicht eingebunden, es wurde keine echte Nachricht versandt.

## Isolation, Firewall und Ressourcen

UFW und DOCKER-USER sind **exakt unverändert**. Alle vorherigen IPv4-/IPv6-Filterregeln blieben in derselben relativen Reihenfolge erhalten. **Docker ergänzte beim genehmigten eigenen Stack seine eigenen Bridge-/Loopback-NAT-Regeln.** Die gesamte iptables-Ausgabe ist deshalb nicht bytegleich; es wurden keine manuellen Firewallkommandos angewendet und keine produktiven Regeln/Portfreigaben geändert. Detaillierter lokaler Nachweis: `DOCKER_OWNED_FIREWALL_ADDITIONS.json`.

Die Apps laufen ohne zusätzliche Capabilities, ohne privilegierten Modus, mit Read-only-Root-Dateisystemen und begrenzten tmpfs-/PID-/CPU-/RAM-Ressourcen. Keine rohen Secrets in `docker inspect`-Umgebungsvariablen; DSN wird erst innerhalb des Prozesses aus neuen Secretdateien zusammengesetzt und auf den festen Staging-DB-Host begrenzt.

Zum Abschluss rund **6007 MiB RAM verfügbar**, weiterhin 173 GiB frei. Momentaner Staging-RAM-Verbrauch: API etwa 78 MiB, Web 71 MiB, Admin 70 MiB, DB 56 MiB; zusammen etwa 275 MiB. Konfigurierte dauerhafte Obergrenzen: 1.5 GiB und 1.75 CPU. Keine OOM-/Ressourcenstörung beobachtet. Gemeinsamer Kernel, Datenträger und Netzkarte bleiben Grenzen der Hostisolation; das ist keine Freigabe für Lasttests oder öffentliche Relay-Nutzung.

## Aufgedeckte und behobene Vorbereitungs-/Testfehler

1. Beim ersten Start wurden unquotierte tmpfs-Optionen als mehrere YAML-Listenelemente gelesen. Docker brach vor dem Dienststart ab. Nur Staging-Compose korrigiert und die Konfigurationsprüfung um absolute, begrenzte tmpfs-Mounts erweitert. Der anschließende echte Start gelang unter den vorgesehenen Limits.
2. Mein erster Signatur-Manipulationstest änderte das letzte Base64-Zeichen. Dabei können lediglich unbenutzte Bits geändert werden, während die Signaturbytes identisch bleiben. Der Testlauf brach dort ab. Der korrigierte Test kippt ein Byte der dekodierten Signatur und wird korrekt abgewiesen. **An der Anwendungs-Signaturprüfung wurde nichts geändert.** Genau der zuvor erzeugte Smoke-Raum wurde im gestoppten eigenen API-Prozess bereinigt; Keys, Benutzer und Lizenzen blieben erhalten.

Der anschließend vollständig erfolgreiche Lauf ist in `LIVE_SMOKE_RESULTS.json` dokumentiert. Keine ungeprüfte automatische Fortsetzung nach fehlgeschlagenem Test; Korrektur und Neustart betrafen ausschließlich Staging.

## Konkrete Übergabe an Claude

`CLAUDE_LOCAL_STAGING_HANDOFF.md` beschreibt den Ist-Zustand und die Clientgrenzen. `staging-license-public-keys.json` und `staging-wireguard-public-key.json` enthalten nur öffentliche Werte.

- **Jetzt nutzbar im Source-Modus auf dem VPS:** `http://127.0.0.1:18510`, normale unveränderte Loopback-Ausnahme des Clientcodes; `local-source-client.json`.
- **Späterer gesonderter Windows-Staging-Build:** `claude-staging-licensing.json` als eingebettete Lizenzkonfiguration mit den neuen Staging-Pins und `https://lan-staging-api.akeno.online`/`https://lan-staging.akeno.online`. Diese Adressen sind derzeit nicht aktiv.
- Ein eingefrorener Build akzeptiert kein HTTP und ignoriert Runtime-Endpoint-Overrides. Keine TLS-Prüfung deaktivieren, keine Produktionsvariablen/Benutzerprofile umstellen. Vor PyInstaller eigenständigen Staging-Buildpfad konfigurieren und danach eingebetteten Endpoint/Key prüfen. Bestehender öffentlicher Release-Pin bleibt in seiner separaten Datei erhalten.
- SSH `-L` kann später die HTTP-Kontroll-API zum lokalen Quellcode-Client tunneln, **kein UDP**. Das ersetzt keinen Zwei-PC-LAN-Test. Testkennwörter werden nicht im Chat/GitHub dokumentiert; für reale Tester später sicher festlegen/übergeben.

## Spätere sichere öffentliche Aktivierung – noch nicht freigegeben

1. Ausdrückliche neue Freigabe, zwei bestätigte öffentliche Tester-/32-IPv4-Adressen und Testzeitraum; erneute Produktions-/Ressourcenprüfung und isoliertes Staging-Backup/Restore planen. Keine private Sicherung in GitHub.
2. Providerfilter prüfen, begrenzte Source-IP-ACL und gemeinsames UDP-Paketbudget nach `FIREWALL_REVIEW.md` kontrolliert einrichten; keine anonyme Freigabe und keine pauschale UFW-Portöffnung.
3. Ausschließlich die drei geplanten Staging-A-Records und zwei HTTPS-Caddy-Hosts nach gesonderter Freigabe aktivieren; Zertifikate normal verifizieren, Admin bleibt SSH/Loopback.
4. **Die gegenwärtige lokale Ankündigung `AKENO_STAGING_UDP_HOST=127.0.0.1` muss ausschließlich in der Staging-Konfiguration auf `lan-staging-udp.akeno.online` umgestellt werden.** Der externe und innere UDP-Port müssen beide 3478 sein. Erst nach ACL-Prüfung den separaten öffentlichen UDP-Override verwenden. Keine Produktions-Compose-Datei anfassen.
5. Echte PCs mit eigenem Testbenutzer/AKENO_HOME, aktiver separater Testlizenz und offizieller WireGuard-Installation verwenden. HTTP-, TLS-, Geräte-/Ticket- und UDP-Negativtests erneut ausführen. Danach P2P/Relay, echtes Spiel, SMB/RDP-Sperre, Widerruf, GUI/UAC, Crash-Recovery und Routing/DNS/IPv6 auf realen Windows-PCs hinter unterschiedlichen Anschlüssen prüfen.

Heute nicht nachgewiesen: echtes Windows/WireGuardNT/UAC, Internet-P2P, ISP-NAT, öffentliche UDP-Erreichbarkeit, Staging-HTTPS, Standby/WLAN-Wechsel und echte Spiele. Kein allgemeiner VPN-Exit-Dienst oder automatisches Release daraus ableiten.

## Betrieb und sicherer Stopp

Der Stack darf gemäß jetziger Freigabe lokal weiterlaufen. `restart: no` verhindert automatische Wiederaufnahme nach Docker-/VPS-Neustart. Keine Timer oder automatische Bereitstellung eingerichtet.

```bash
cd /srv/akeno-network-staging/source/deploy/network-staging
sudo python3 monitor-staging.py
sudo docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml --profile approved-staging stop staging-api staging-web staging-admin staging-db
```

Der Stop-Befehl ist eine **optionale** spätere Stilllegung, wurde am erfolgreichen Abschluss nicht ausgeführt. Er löscht keine Daten. Für Entfernung der eigenen Container/Netze kann nach Prüfung nur dieses Projekt mit `down` ohne `-v`, `--rmi` oder globale Prune-/Firewall-Restore-Befehle abgebaut werden; Volumes und Secrets bleiben für Wiederaufnahme erhalten. Keine vorhandenen Produktionscontainer stoppen.

## Nachweise und GitHub

`HEALTH_CHECKS.json`, `LIVE_SMOKE_RESULTS.json`, `PUBLIC_SMOKE_SUMMARY.json`, `POSTFLIGHT.json`, `RUNNING_SOURCE_IDENTITY.json` und Secret-Dateimetadaten belegen den beschriebenen Stand. Ausführliche Produktionsinventur und rohe Firewall-/Mountmetadaten bleiben im lokalen Paket. GitHub enthält nur Vorlagen/Testcode, öffentliche Testkeys, bereinigte Zusammenfassungen und Dokumentation. Letzten zurückgelesenen Remote-Commit siehe `GITHUB_CHECKPOINT.json`; es wurde kein PR, Merge, Tag oder Release erstellt.
