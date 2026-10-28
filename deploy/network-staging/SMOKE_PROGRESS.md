# Lokaler AKENO-Network-Smoke-Test

Freigabe: ausschließlich isolierter VPS-Loopback-Betrieb. Kein DNS/Caddy, keine öffentliche UDP-Freigabe, keine produktiven Benutzer/Lizenzen/Containeränderungen.

Geprüfte Quelle: Claude `588b21ca64a78f13d937a89abb58d5289b34f7c5`; bestätigte Vorbereitung `555092eb67e692c209a0681e38a175c43b77d531` auf `codex/akeno-network-staging-20261028`.

Vorprüfung: sieben bestehende Container in Betrieb, produktive HTTPS-/Health-/Ready-Prüfungen erfolgreich, rund 6,2 GiB RAM und 174 GiB Speicher verfügbar. Separate verifizierte Arbeitskopie unter `/srv/akeno-network-staging` erstellt; eigenes App-Image wird gebaut. Noch keine privaten Staging-Schlüssel oder Konten erzeugt.

Docker verwaltet später ausschließlich eigene Staging-Bridge-/Loopback-NAT-Regeln. Bestehende UFW-/DOCKER-USER-Regeln und produktive Portzuordnungen werden nicht verändert. Öffentlicher UDP-Override wird nicht verwendet.

## Lokaler Stack initialisiert

- Eigenes Image gebaut: `sha256:53de16b05d304b34ec88706d9316f9cdf36ac2355b05bf37b72fd6fb97c3ab50`.
- Der erste Start brach wegen falsch aufgeteilter YAML-tmpfs-Einträge vor dem Dienststart ab. Nur Staging korrigiert; Konfigurationsprüfung um absolute tmpfs-Mounts/Schutzoptionen ergänzt.
- Separate PostgreSQL-/Raum-/Key-/Mail-Volumes initialisiert; API sowie DB gesund, Web/Admin lokal gestartet.
- Neuer Staging-Lizenzkey `92c8ea977d38cd6c`, ausdrücklich verschieden vom produktiven Lizenzkey. Private Werte bleiben ausschließlich in Staging-Volumes/geschütztem runtime-Verzeichnis.
- Zwei synthetische Standardnutzer mit befristeten Testlizenzen angelegt; Kennwörter nur im rootgeschützten lokalen Secret-Speicher. Kein SMTP-Versand, kein Administrator angelegt.
- Ausschließlich Loopback: TCP 18510/18511/18512 und UDP 3478. Keine DNS-, Caddy-, UFW- oder DOCKER-USER-Regeländerung.

## Live-Client-Prüfung

- Aktueller Claude-LicenseManager meldet beide Konten mit aktiven, kryptografisch geprüften Lizenzen an und bindet getrennte Testgeräte.
- Raum erstellen/beitreten, authentifizierte Heartbeats und Ablehnung fremder Geräte/Actor-Spoofing erfolgreich.
- Ein Testfehler beim Verändern des letzten Base64-Zeichens wurde erkannt: nicht kanonische letzte Zeichen können identische Signaturbytes darstellen. Test geändert auf tatsächliches Bitflip eines dekodierten Signaturbytes, ohne Anwendungsprüfung zu ändern.
- Der Lauf brach am fehlerhaften Test ab; nur API angehalten, genau der zugehörige Staging-Smoke-Raum bereinigt, Schlüssel/Konten/Lizenzen erhalten. Erneuter vollständiger UDP-/Client-Lauf folgt. Kein produktiver Zugriff des Live-Tests.

## Live-Smoke bestanden

48/48 Prüfungen erfolgreich, inklusive 64 echten Ende-zu-Ende-verschlüsselten 1.200-Byte-Roundtrips über den laufenden lokalen Koordinator/Relay. Sicherheitsprüfungen unverändert; Signaturmanipulation verfälscht jetzt tatsächlich ein Signaturbyte. Testräume gelöscht, Testgeräte widerrufen, alle Testsessions beendet. Vier Staging-Container gesund; sieben vorhandene Container und alle 19 Produktionstabellen unverändert. UFW/DOCKER-USER und alle bisherigen Filterregeln erhalten; Docker ergänzte nur eigene private Staging-Netzregeln. Öffentliche Portverbindungen auf die TCP-Testports scheitern. Kein externer Scan oder echter Windows-/ISP-NAT-Test behauptet.

## Abschlusskontrolle

Alle Prüfungen bestanden: vier Staging-Dienste gesund, sieben vorhandene Container und 19 Produktionstabellen unverändert; Produktionskonfigurationshashes identisch. UFW/DOCKER-USER unverändert, vorherige IPv4-/IPv6-Regeln in gleicher Reihenfolge erhalten. Nur Docker-eigene private Staging-Regeln hinzugekommen. Keine öffentlichen DNS-/Proxy-/UDP-Freigaben. Öffentliche Clientpins und Grenzen dokumentiert; historische Vorbereitungsdokumente mit aktuellem Status versehen. Letzter bestätigter Checkpoint vor diesem Update: `999067b39ba45d400f35869a2189a3bab3add799`.
