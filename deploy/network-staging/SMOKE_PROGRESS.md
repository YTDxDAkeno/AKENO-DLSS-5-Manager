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
