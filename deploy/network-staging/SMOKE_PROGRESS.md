# Lokaler AKENO-Network-Smoke-Test

Freigabe: ausschließlich isolierter VPS-Loopback-Betrieb. Kein DNS/Caddy, keine öffentliche UDP-Freigabe, keine produktiven Benutzer/Lizenzen/Containeränderungen.

Geprüfte Quelle: Claude `588b21ca64a78f13d937a89abb58d5289b34f7c5`; bestätigte Vorbereitung `555092eb67e692c209a0681e38a175c43b77d531` auf `codex/akeno-network-staging-20261028`.

Vorprüfung: sieben bestehende Container in Betrieb, produktive HTTPS-/Health-/Ready-Prüfungen erfolgreich, rund 6,2 GiB RAM und 174 GiB Speicher verfügbar. Separate verifizierte Arbeitskopie unter `/srv/akeno-network-staging` erstellt; eigenes App-Image wird gebaut. Noch keine privaten Staging-Schlüssel oder Konten erzeugt.

Docker verwaltet später ausschließlich eigene Staging-Bridge-/Loopback-NAT-Regeln. Bestehende UFW-/DOCKER-USER-Regeln und produktive Portzuordnungen werden nicht verändert. Öffentlicher UDP-Override wird nicht verwendet.
