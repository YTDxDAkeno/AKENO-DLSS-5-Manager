# Modul-Lizenzen: Migration des Lizenzservers (VPS) — Anleitung, Prüfung, Rückweg

Gilt für den Lizenzserver aus diesem Repository (`server/`, `deploy/`) mit PostgreSQL 16 (Docker Compose).
**Nichts davon wurde auf dem produktiven VPS ausgeführt.** Jeder Schritt braucht deine Freigabe.

## Was sich ändert

* **Datenbank (Migration `0002_feature_entitlements`, nur additiv):**
  * neue Tabellen `feature_grants` (ein Modul pro Konto: Typ, Status, Ablauf, PC-Limit) und `feature_grant_events`
    (Verlauf);
  * `license_codes`: neue Spalten `entitlement` (Standard `akeno-suite`, alle vorhandenen Codes bleiben Full-Codes)
    und `duration_days` (eigene Laufzeit, leer bei allen vorhandenen Codes);
  * `license_redemptions`: `license_id` darf leer sein (Modul-Einlösung), neue Spalte `grant_id`;
  * `offline_certificates`: `license_id` darf leer sein, neue Spalten `user_id` (aus der Lizenz übernommen) und
    `entitlements` (leer = bisheriges Suite-Zertifikat).
  * Keine vorhandene Zeile wird geändert, außer dass Zertifikate ihr Konto (`user_id`) eingetragen bekommen.
    Laufzeiten, Codes, Geräte, Audit-Einträge bleiben unverändert (Test:
    `test_migration_0002_keeps_existing_licenses_and_its_downgrade_withdraws_module_codes`).
* **API (rückwärtskompatibel):** AKENO 3.1.1 bekommt exakt die bisherigen Antworten (nur für AKENO Full). Ein
  3.1.1-Client, der einen Modul-Code einlösen will, bekommt `update_required`; der Code bleibt unbenutzt. AKENO 3.2.0
  funktioniert auch mit dem alten Server (nur AKENO Full).
* **Admin-Panel:** Produkt/Modul und Laufzeit bei der Codeerzeugung, Modul-Karte auf der Benutzerseite, neuer
  Schalter „AKENO-VPN-Lizenzen dürfen ausgegeben werden“ (aus; nur Super-Admin).
* **LAN Play / AKENO VPN (Server):** prüfen jetzt ihr eigenes Modul (AKENO Full erlaubt weiterhin alles).

## Reihenfolge (verbindlich)

### 1. Backup und Vorprüfung
```bash
cd /opt/akeno-licensing/deploy
```
```bash
./backup.sh
```
```bash
docker compose run --rm akeno-api akeno-server check
```
Notiere die Zeile `records` (Anzahl users, licenses, license_codes, license_redemptions, devices,
offline_certificates) und `schema 0001`. Das Backup (Datenbank-Dump und Signaturschlüssel) liegt in
`deploy/backups/`; kopiere es zusätzlich auf deinen PC.

### 2. Rückwärtskompatible Servererweiterung einspielen
Auf deinem PC (im Repository, Branch mit diesem Stand):
```bash
deploy/bundle.sh
```
Bundle und `.sha256` auf den VPS kopieren (z. B. `deploy/push-to-vps.sh`), dann auf dem VPS:
```bash
./update.sh --bundle /opt/akeno-licensing/incoming/<bundle>.tar.gz --sha256 <hash>
```
`update.sh` macht vorher ein Backup, behält die vorige Version in `.previous/` und startet neu.

### 3. Datenbankmigration
Läuft beim Start des API-Containers automatisch (`akeno-server migrate`). Prüfen:
```bash
docker compose logs akeno-api | grep "database migrated"
```
```bash
docker compose run --rm akeno-api akeno-server check
```
Erwartet: `schema 0002 (current)`; die Zahlen in `records` sind **dieselben** wie in Schritt 1, dazu
`feature_grants 0`.

### 4. Server- und Admin-Funktionstests (produktiv, ohne Kunden zu berühren)
1. `https://api.akeno.online/api/v1/system/version` antwortet; der Signaturschlüssel ist unverändert
   (`akeno-server keys list`).
2. Admin-Panel → Aktivierungscodes: „Produkt / Modul“ und „Laufzeit“ sind da; AKENO VPN ist „gesperrt“.
3. Admin-Panel → Benutzer → ein bestehendes Konto mit Full-Lizenz: Karte „AKENO Full“ unverändert, Karte „Module“
   zeigt „Keine Lizenz“.
4. Mit einem **eigenen Testkonto**: einen Code „AKENO LAN Play · 30 Tage“ erzeugen, auf der Website einlösen,
   in der Modul-Karte prüfen, danach „Widerrufen“.
5. Mit AKENO **3.1.1** auf einem PC mit bestehender Lizenz: Lizenz → „Jetzt prüfen“ → weiterhin aktiv.

### 5. Neuen Windows-Client freigeben
Erst nach Schritt 4: AKENO 3.2.0 offline signieren (bestehendes Verfahren) und veröffentlichen. Bestehende
Full-Lizenzen funktionieren nach dem Update weiter (auch offline mit Lifetime-Zertifikat).

### 6. Neue Code-Typen produktiv verwenden
* AKENO LAN Play und AKENO Full mit 90/365/eigenen Tagen: sofort nach Schritt 5.
* **AKENO VPN: erst nach geklärter Nutzungserlaubnis von VPN Gate** (siehe Abschlussbericht). Dann als Super-Admin:
  Einstellungen → „AKENO-VPN-Lizenzen dürfen ausgegeben werden“ einschalten.

## Überwachung nach dem Deployment
* `akeno-server check` (Schema, Zahlen), `docker compose logs --since 1h akeno-api` (Fehler 5xx).
* Admin-Übersicht: aktive Lizenzen unverändert, „Prüfungen 24 h“ wie vorher, Statistik `module_grants`.
* Audit-Log: `codes.generate` mit `entitlement`, `grant.*`-Einträge.

## Rückweg
* **Code zurück, Daten behalten:**
  ```bash
  ./update.sh --rollback
  ```
  Neu: Vorher führt `update.sh` mit dem neuen Code `akeno-server migrate-down 0001` aus. Das entfernt
  Modul-Lizenzen und ihren Verlauf, zieht **unbenutzte Modul-Codes zurück** (der alte Server würde sie sonst als
  Full-Codes behandeln) und löscht Einlösungen/Zertifikate, die nur zu Modulen gehörten. Full-Lizenzen, Codes,
  Geräte, Konten bleiben.
* **Alles auf den Stand vor dem Update (Datenverlust seit dem Update):**
  ```bash
  ./restore.sh backups/akeno-db-<stamp>.dump backups/akeno-keys-<stamp>.tar.gz
  ```
  danach `./update.sh --rollback`.
* **Nie** den alten Server-Code auf die migrierte Datenbank setzen, ohne den Rückweg oben zu nehmen.

## Getestet
* SQLite und PostgreSQL 16 (CI): Migration passt exakt zu den Modellen, Downgrade bis `base` und zurück.
* SQLite mit vorhandenen 3.1-Daten: Upgrade erhält alle Werte; Downgrade zieht Modul-Codes zurück.
* `update.sh --rollback` ruft `migrate-down` vor dem Wechsel auf (`test_rollback_takes_the_database_back_…`).
* Nicht getestet: der produktive VPS selbst (keine Freigabe).
