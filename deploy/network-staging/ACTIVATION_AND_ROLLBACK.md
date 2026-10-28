# Spätere Aktivierung und Rollback — Eigentümerfreigabe erforderlich

**Diese Anleitung wurde nicht ausgeführt.** Aktuell existieren keine Staging-Container, Volumes, Konten, Datenbanken, Secrets oder DNS-/Firewall-Freigaben. Es ist nur ein prüfbares Vorbereitungspaket. Auch die öffentliche Compose-Erweiterung ist keine Freigabe.

## A. Vorbedingungen und Abbruchkriterien

Vor Aktivierung müssen vorliegen: ausdrückliche Freigabe des Betriebsorts, feste bestätigte Code-/Image-Stände, die beiden Tester-IPv4-Adressen, abgesprochener Testzeitraum, geprüfte Provider-Regeln und ein neuer Vorher-Zustand. Der aktuelle VPS reicht für zwei Tester nach der Momentaufnahme; bei größeren Gruppen, schwankender Produktionslast oder fehlendem Provider-Bandbreitenschutz einen getrennten Testhost verwenden.

Die reservierten Subnetze `172.30.78.0/28` und `172.30.78.16/28`, TCP 18510/18511/18512 und UDP 3478 müssen beim tatsächlichen Start erneut frei sein. Docker- und Host-Routing prüfen, nicht nur Containerlisten. RAM verfügbar mindestens 3 GiB, Dateisystem mindestens 20 GiB frei; andernfalls abbrechen. Das ist eine vorsichtige lokale Startschwelle, kein belegter Leistungstest. Swap ist aktuell nicht vorhanden.

Keine vorhandene Installation unter `/opt/akeno-licensing` als Build-Kontext, .env, Compose-Datei, Secret-Quelle oder Volume verwenden. Insbesondere das Upstream-LAN-Override nicht mit dem Produktionsstack kombinieren. Keine globalen Docker-Prune-, Netzwerkbereinigungs- oder Firewall-Restore-Befehle.

## B. Getrennter Arbeitsordner und Image — erst nach Freigabe

Ein eigener Checkout, beispielsweise `/srv/akeno-network-staging/source`, muss den bestätigten Codex-Staging-Checkpoint enthalten und auf dem dokumentierten Claude-Commit basieren. Clone/Checkout mit dem bereits vorgesehenen Git-Zugang; keine Zugangsdaten als Shell-Argument. Vor Build `git diff 588b21ca64a78f13d937a89abb58d5289b34f7c5 -- server` kontrollieren: die Vorbereitung verändert den Server-Code nicht. Den zu diesem Zeitpunkt freigegebenen vollständigen SHA statt eines beweglichen Branchnamens festhalten.

```bash
# Ausschließlich in diesem separaten Checkout; Befehle sind eine zukünftige Arbeitsanweisung.
AKENO_STAGE_SOURCE=/srv/akeno-network-staging/source
cd "$AKENO_STAGE_SOURCE/deploy/network-staging"
cp --no-clobber .env.example .env
sudo python3 check-preparation.py
# Die später zu benutzenden Image-Digests und Abhängigkeiten festhalten.
sudo docker build --tag akeno-network-staging:588b21c "$AKENO_STAGE_SOURCE/server"
sudo docker image inspect akeno-network-staging:588b21c --format '{{.Id}}'
sudo docker image inspect postgres:16-alpine --format '{{json .RepoDigests}}'
```

Der Image-Tag wird hier nur vorbereitet; der Build ist nicht erfolgt. Vor Einsatz immutable Image-ID/Digest dokumentieren und Image/Paketversionen prüfen. Der aktuell lokal vorhandene PostgreSQL-Digest ist in Compose fest gepinnt; `pull_policy: never` verhindert unbeaufsichtigtes Nachladen. Fehlt dieses Image auf einem anderen Host, zuerst separat die genehmigte identische Version bereitstellen. Das vorhandene PostgreSQL-16-Alpine verwendet UID/GID 70; `prepare-secrets.py` baut darauf auf. Bei Imagewechsel die UID erneut prüfen.

## C. Lokales Staging initialisieren — erst nach Freigabe

```bash
# Noch immer ausschließlich im separaten deploy/network-staging-Verzeichnis.
sudo python3 prepare-secrets.py
sudo docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml --profile initialize-only run --rm --no-deps staging-init
sudo docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml --profile approved-staging up -d staging-db staging-api staging-web staging-admin
sudo docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml --profile approved-staging exec -T staging-api python /staging/entrypoint.py akeno-server keys init
```

Die Secret-Erzeugung überschreibt nichts und verweigert ein vorhandenes `runtime/`-Verzeichnis. Bei Teilfehlern bleibt sie absichtlich stehen; vorhandene Dateien nicht automatisch löschen oder neu erzeugen. Root-Verzeichnis und `runtime/secrets` sind 0700, Secretdateien 0400 und dem jeweils notwendigen Container-UID zugeordnet. Nur neue Staging-Secrets, niemals Produktionssecrets. Das PostgreSQL-Passwort wird als Datei eingelesen; die App setzt ihre feste Staging-DSN erst im eigenen Prozess. Das Passwort erscheint nicht in Compose/Inspect-Konfiguration oder Shell-Argumenten. Ein administrativer Hostbenutzer mit uneingeschränktem sudo kann technisch trotzdem auf diese Staging-Dateien und Prozesse zugreifen.

Der Initialisierungscontainer ist kurzlebig, netzwerklos und hat nur Rechte zum Eigentümerwechsel der drei **neuen Staging-Volumes**. Der einzige API-Worker führt Migrationen nur auf `staging-db/akeno_network_staging` aus. Netzwerk-Raumdaten liegen separat in SQLite. Die API erstellt einen neuen lokalen X25519-Koordinatorschlüssel; `keys init` erstellt einen neuen Ed25519-Lizenz-/LAN-Ticketschlüssel. Es gibt keinen Release-Privatschlüssel und keine Release-Key-Erzeugung.

Nun ausschließlich öffentliche Testkeys exportieren:

```bash
sudo docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml --profile approved-staging exec -T staging-api python /staging/entrypoint.py akeno-server keys export --server-url https://lan-staging-api.akeno.online
```

Die JSON-Ausgabe ist öffentlich. Als `staging-license-public-keys.json` übertragen und Key-ID über SSH unabhängig bestätigen. Sie darf nach Prüfung an Claude gehen; kein privater Schlüssel verlässt den Staging-Datenträger. Schlüsselwerte existieren in dieser Vorbereitung noch nicht und wurden nicht erfunden.

Zwei synthetische normale Testnutzer vorbereiten, ohne Kennwörter als Argument/Datei auszugeben:

```bash
sudo -v
python3 prompt-test-passwords.py | sudo docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml --profile approved-staging exec -T staging-api python /staging/seed-test-users.py
sudo python3 monitor-staging.py
```

Nicht mit `tee`, Shell-Tracing oder einem aufgezeichneten Terminal kombinieren. Adressen: `pc-a@akeno-staging.invalid`, `pc-b@akeno-staging.invalid`; normale Benutzer, sieben Tage Lizenz, je ein Gerät. Keine echten E-Mails: Backend ist `file`, SMTP-Secrets fehlen absichtlich. Die Vorab-Verifizierung dieser synthetischen Accounts gilt nur für Staging. Bei bereits vorhandenen Benutzern bricht das Seed-Skript ab und verändert keinen bestehenden Account. Keine produktiven Konten kopieren.

Admin wird für diese zwei Nutzer nicht benötigt. Falls später eine Staging-Administration gebraucht wird, bedarf es einer ausdrücklich gewählten Staging-Adminidentität und der bestehenden CLI plus 2FA. Kein Administratorkonto wird automatisch erstellt. Zugriff ausschließlich über SSH-Tunnel:

```powershell
ssh -N -L 18512:127.0.0.1:18512 administrator@85.190.112.10
```

Im separaten Testbrowser `http://localhost:18512` verwenden. Nur dieser Loopback-Admin nutzt `cookie_secure=false`; API/Web bleiben TLS-/Secure-Cookie-konfiguriert. Kein öffentlicher Admin-DNS-Eintrag oder Proxy-Host.

## D. Öffentliche Testpfade — separate ausdrückliche Freigabe

Diesen Abschnitt **nicht** ausführen, bevor Quelle-IP-ACL, globales UDP-Budget, DNS, TLS und Testzeitraum freigegeben sind.

1. Nur drei A-Records anlegen: `lan-staging-api`, `lan-staging`, `lan-staging-udp` unter `akeno.online`, jeweils `85.190.112.10`, anfangs TTL 300. Keine AAAA-Records. UDP-Host nicht durch einen HTTP/CDN-Proxy schicken. Diese Records waren bei der Bestandsaufnahme nicht auflösbar.
2. `FIREWALL_REVIEW.md` prüfen. Die vorgesehene DOCKER-USER-ACL mit den beiden tatsächlichen /32-Quellen und dem gemeinsamen Paketbudget installieren und prüfen. Erst dann öffentliche UDP-Bindung. Kein `ufw allow 3478/udp` auf Verdacht. Provider-Firewall separat prüfen.
3. Im vorbereiteten Caddy-Fragment die beiden Dokumentations-/32-Adressen durch die bestätigten Tester-IP-Adressen ersetzen. Noch nicht produktiv importieren, zuerst geändertes Fragment und zusammengeführte Konfiguration prüfen. Aktuelle Produktionsdatei sichern, dann gezielt ein separates Staging-Fragment importieren; keine Produktionshost-Blöcke ersetzen. Erst nach erneuter Konfigurationsprüfung bewusst Caddy neu laden. Dies ist eine spätere genehmigungspflichtige Produktionsproxy-Änderung. Die jetzige Vorbereitung führte nur `caddy adapt` aus.
4. Öffentlich vertrauenswürdige Zertifikate für die zwei HTTPS-Testhosts ausstellen lassen. Zugriff von beiden erlaubten Testern und 403 von einer nicht erlaubten Quelle prüfen. Keine Zertifikatsvalidierung im Client deaktivieren.
5. Erst anschließend mit dem separaten Override die UDP-Bindung erweitern:

```bash
export AKENO_STAGING_PUBLIC_IPV4=85.190.112.10
sudo env AKENO_STAGING_PUBLIC_IPV4="$AKENO_STAGING_PUBLIC_IPV4" docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml -f compose.public-udp.yml --profile approved-staging up -d staging-api
```

6. Nicht angemeldetes `POST /api/v1/network/lan/rooms` muss vom zugelassenen Tester HTTP 401 liefern. Danach mit den zwei echten PCs und Staging-Accounts den vollständigen Plan in `WINDOWS_HANDOFF.md` abarbeiten. Der authentisierte Heartbeat muss `transport: wireguard`, Koordinator, gültiges Ticket und nach Handshake den korrekten öffentlichen Geräteendpoint liefern. Fehlende Beweise sind Abbruchgründe, kein Anlass, Sicherheitsregeln zu lockern.

## E. Logging, Monitoring und Backups

Docker-Logs sind auf drei Dateien à 10 MB je Service begrenzt. HTTP-Access-Logs sind bewusst nicht aktiviert; keine Authorization-Header, Passwörter, Invite-/Peer-Tickets, Profile oder Paketnutzdaten protokollieren. App-Fehler/Startmeldungen und Ressourcenwerte genügen für den ersten Test. Tracebacks nicht ungeprüft an Support weitergeben.

`monitor-staging.py` liest nur Projektstatus und lokale Health-/Ready-Endpunkte. Ergänzend `docker stats --no-stream`, `df -h`, OOM-/Restart-Zähler und CPU-/RAM-Peaks beobachten. Konservative Warnschwellen: Host-RAM verfügbar <2 GiB, freier Speicher <15 GiB, Staging >80 % seines Memory-Limits, anhaltende API-Zeitüberschreitungen oder Produktions-Health-Fehler. Bei Produktionsproblemen den Stagingtest beenden. Die `Coordinator.stats`-Zähler existieren derzeit nur im API-Prozess; ein abgesicherter Metrics-Exporter ist nicht implementiert. Keine öffentliche Debug-/Stats-Route erfinden.

Die Produktions-Backupautomatisierung wird nicht erweitert. Staging erhält später eigene manuelle Backups unter einem rootgeschützten separaten Verzeichnis, mit eigener Aufbewahrung und Verschlüsselung. Konsistenter Ablauf: nur Staging-API/Web/Admin anhalten, PostgreSQL per `pg_dump --format=custom` sichern, Netzwerk-SQLite und eigene Lizenz-/Koordinatorschlüssel aus den drei Staging-Volumes sichern, Hashliste erzeugen, Dump-Index prüfen und Wiederherstellung in einem weiteren wegwerfbaren Projekt testen. Erst dann Staging-Dienste bewusst wieder starten. Keine Laufzeitkopie einer offenen SQLite-Datei als konsistente Sicherung behaupten. Die private Test-Lizenzsignatur ist kein Produktions-Release-Schlüssel; dennoch Backups als Secrets verschlüsseln. Noch kein Backup-Job/Timer oder neues Archiv angelegt.

Die bestehende Produktionssicherung vom 28.10.2026, 01:31:20 CET, wurde nur lesend geprüft: DB-Dump und beide Key-/Konfigurationsarchive sind indizierbar. Ein neuer vollständiger Produktions-Restore wurde in dieser Vorbereitung nicht ausgeführt; zuvor bestehende Wiederherstellungsnachweise werden dadurch nicht ersetzt.

## F. Rollback und sichere Stilllegung

Bei Fehlern stoppen, keine automatische Neuinstallation. Test zuerst auf den PCs beenden/recovern, dann nur diese Staging-Dienste anhalten:

```bash
sudo docker compose -p akeno-network-staging --env-file .env -f compose.staging.yml --profile approved-staging stop staging-api staging-web staging-admin staging-db
```

Dieser Befehl löscht keine Volumes und setzt keine Produktionscontainer zurück. `restart: "no"` verhindert einen unbeabsichtigten Dienststart nach Docker-/Host-Neustart. Für längerfristigen Abbau kann nach Zustandsprüfung ausschließlich dieses Projekt mit `down` **ohne** `-v`, `--rmi` oder globale Prune-Befehle entfernt werden. Eigene Volumes, Secrets und Sicherungen bleiben erhalten; Löschung bedarf einer separaten Freigabe.

Falls später öffentlich aktiviert: exakten Staging-DOCKER-USER-Sprung und nur die zwei eigenen ACL-Chains entfernen; nur Staging-Caddy-Import rückgängig machen und Proxykonfiguration erneut prüfen; nur die drei Staging-DNS-Einträge entfernen. Keine alte globale Caddy-/Firewall-Konfiguration über zwischenzeitliche Änderungen anderer Arbeiten schreiben. Danach Produktions-HTTPS, API-Health/Ready, Containeridentitäten, Datenbankzustand und letzten Backup-Erfolg kontrollieren.

**Aktueller Rollbackbedarf: keiner.** Die jetzige Vorbereitung änderte lediglich Arbeits-/Dokumentationsdateien und den eigenen GitHub-Entwicklungsbranch; ein laufendes Staging gibt es nicht.
