# Übergabe an Codex: was auf dem VPS für AKENO 3.0 noch zu tun ist

Stand: 2026-10-24. Branch `claude/clever-fermi-xykqbw` (auf PR #15 → #14 → #13). Auf GitHub ist nichts gemergt,
nichts veröffentlicht und auf dem VPS nichts verändert.

Diese Datei ist die Reihenfolge. Die Details stehen in:

| Datei | Inhalt |
|---|---|
| `CODEX_ACCOUNTS_HANDOVER.md` | Konto-Änderungen: Zweck jedes Hunks, Konfliktrisiko, Vertrag, Konfiguration, Abnahme |
| `accounts-server.patch` | Patch für `server/akeno_server/` (9 Dateien). SHA-256 `d077080a16d66d672f367c1698ba3901c2fa17705a9e784e3e16035666095f7f` |
| `CODEX_RELEASE_MANAGER.md` | Älterer Vorschlag mit statischen, signierten Kanal-Dateien. Durch den echten Release Manager überholt (Abschnitt B) |
| `PRODUCTION_STATUS.md` | Was die Lese-Prüfung gegen die Produktion zuletzt gesehen hat |

## Grundregeln

1. **Eure Sicherheitskorrekturen auf dem VPS gehen vor.** Sie liegen in keinem Branch dieses Repositorys. Deshalb:
   * den Server dieses Branches **nicht** komplett ausrollen;
   * `deploy/` aus diesem Repository **nicht** über die Produktion kopieren;
   * keine Container mit dem Image dieses Branches ersetzen.

   Nur den Patch gezielt auf euren Produktionsstand anwenden. Bei einem Konflikt bleibt euer Code. Ihr baut nur das
   Verhalten nach, das `CODEX_ACCOUNTS_HANDOVER.md` §3 für diesen Hunk verlangt.
2. **Keine Produktionsdaten ändern.** Der Patch bringt keine Migration, keine neue Tabelle und keine neue Spalte mit.
   Vorher trotzdem `deploy/backup.sh` ausführen und den laufenden Image-Tag notieren.
3. **Kein privater Schlüssel in die App, keine Schlüssel tauschen:**
   * Die App enthält nur öffentliche Schlüssel:
     * Lizenz: `f382ecda000888b2`;
     * Release: `d6139235b220a4aa`.

     Der Windows-Build prüft das bei jedem Lauf (`tools/check_build_config.py`). Er prüft auch, dass beide
     Schlüssel verschieden sind.
   * Den Release-Signierschlüssel `d6139235b220a4aa` und den Lizenzschlüssel **nicht** neu erzeugen oder ersetzen.
     Ausgelieferte Apps vertrauen nur diesen öffentlichen Schlüsseln.
   * Wo der private Release-Schlüssel liegt, siehe B, Punkt 4. Empfehlung: nicht auf dem VPS.

## A. Fehlende Konto-Endpunkte (aus PR #14)

Die Produktion antwortet heute mit 404 auf:
* `POST /api/v1/auth/change-email`
* `POST /api/v1/auth/sessions/revoke-others`

Außerdem meldet sie den ehrlichen Versandstatus (`verification_email`) nicht.

Schritte (ausführlich in `CODEX_ACCOUNTS_HANDOVER.md` §2):

1. Image-Tag notieren. `deploy/backup.sh` ausführen.
2. Kopie des Produktions-Quellstands anlegen, neuer Branch, z. B. `integrate/app-accounts`.
3. Patch prüfen:
   * `git apply --3way --check accounts-server.patch`
   * ohne Git-Historie: `patch -p1 --dry-run`

   Geprüft: Der Patch lässt sich sauber auf den AKENO-3.0-Server `95ac04f` anwenden.
4. Patch anwenden. Abgelehnte Hunks nach §3 von Hand nachbauen. Eure Fixes bleiben.
5. Die Tests aus §1 übernehmen und die gesamte Server-Suite laufen lassen, auf SQLite und auf PostgreSQL 16.
   Eure eigenen Tests müssen grün bleiben.
6. In `.env` setzen (§5):
   * `AKENO_PUBLIC_URL=https://akeno.online`, Pflicht, sonst zeigen die Links in den E-Mails auf die API;
   * SMTP über checkdomain.
7. Ein Image für `akeno-api`, `akeno-web` und `akeno-admin` bauen und ausrollen. Es läuft keine Migration.
8. Abnahme:
   * `python tools/account_acceptance.py check`, nur lesend; danach meldet er die beiden Endpunkte als vorhanden;
   * `python tools/account_acceptance.py flow --email <Testpostfach> --confirm-real-account`. Das legt **ein**
     Testkonto an und verschickt echte E-Mails. Nur mit Freigabe des Inhabers ausführen.
9. Rollback: den vorherigen Image-Tag starten. In der Datenbank muss nichts zurückgenommen werden.

Danach auf GitHub: Actions → „Production read-only check“ → „Run workflow“. Die beiden Zeilen wechseln von INFO
(fehlt) auf OK.

## B. Release Manager ↔ Windows-Updater

### Der tatsächliche Vertrag (aus der Produktion gelesen, 2026-10-24)

Quelle: das öffentliche OpenAPI-Dokument `https://api.akeno.online/api/v1/openapi.json`. Die Produktionsprüfung
(`tools/api_contract.py --release-details`) liest es nur.

| Endpunkt | Antwort heute |
|---|---|
| `GET /api/v1/releases/signing-key` | `{"algorithm": "Ed25519", "key_id": "d6139235b220a4aa", "public_key": "bpe9acmephsoZSYzy12R2XFy_lJZ_69MiV31ekehcdo"}` |
| `GET /api/v1/releases/stable`, `/releases/latest` | 404, noch kein Release veröffentlicht |
| `GET /api/v1/releases` | `{"releases": []}` |
| `GET /api/v1/releases/{version}` | ein Release |

Release-Objekt, `schema_version: 1`:
* `product: "akeno-gaming-suite"`, `platform: "windows-x64"`, `channel: "stable"`, `version` (`X.Y.Z`, keine
  Vorabversionen);
* `release_notes`, `release_date`, `published_at`;
* `download_url`, `size_bytes`, `sha256`;
* `authenticode: {verified: true, publisher_certificate_sha256}`;
* `minimum_supported_version`;
* `unattended_install_allowed: false`;
* `signed_payload`, `signature: {algorithm: "Ed25519", key_id, value}`.

### Was die App jetzt damit macht (dieser Branch)

* Standard-Updatequelle jedes Builds ist der Release Manager: `GET https://api.akeno.online/api/v1/releases/stable`
  (`akeno/data/distribution.json`, `update_source: "akeno-api"`). Der GitHub-Weg ist nur noch per Variable
  `AKENO_UPDATE_SOURCE=github` aktiv. Das Repository `AKENO-Releases` ist ohnehin nicht öffentlich erreichbar.
* Ein Release zählt nur unter diesen Bedingungen (`akeno/updater/manifest.py: parse_api`):
  1. `signature.key_id` ist ein eingebauter Release-Schlüssel. Heute ist das nur `d6139235b220a4aa`. Die
     Lizenzschlüssel zählen hier nicht.
  2. Die Ed25519-Signatur ist gültig über **genau den Text von `signed_payload`** (UTF-8).
  3. `signed_payload` ist ein JSON-Objekt, entweder als JSON-Text oder als base64url davon. Es enthält mindestens
     `product`, `platform`, `channel`, `version`, `download_url`, `sha256`, `size_bytes` und
     `unattended_install_allowed`.
  4. Jedes Feld außerhalb von `signed_payload` muss gleich dem signierten Wert sein. Verwendet werden nur die
     signierten Werte.
  5. `unattended_install_allowed` ist `false`, `channel` ist `stable`, `download_url` ist HTTPS auf `akeno.online`.
  6. Die Version ist nicht älter als eine schon gesehene (Anti-Rollback).

  `signature.value` darf base64url, base64 oder hex sein.
* Ein solches Release wird angezeigt mit Version, Datum, Größe und Notizen. Die App **installiert es nicht
  selbst.** Sie bietet die Downloadseite an: `https://akeno.online/downloads/latest`, der `download_url` aus
  `/system/version`. Der Grund: Der Release Manager beschreibt eine einzelne Datei (den Installer). Der Updater
  installiert nur portable ZIPs, weil nur so der Ordnertausch mit Rollback möglich ist.
* Die Produktionsprüfung schlägt fehl, wenn `/releases/signing-key` einen Schlüssel nennt, der nicht im Build
  steckt. Sobald `/releases/stable` ein Release liefert, verifiziert sie es mit genau diesem Parser.

### Was Codex bestätigen oder liefern muss

1. **Signaturformat bestätigen:** Signiert ist der UTF-8-Text von `signed_payload`, unverändert, und
   `signed_payload` ist JSON mit den Feldern oben. Andernfalls lehnt die App jedes Release ab. Das ist sicher, aber
   dann zeigt sie keine Updates an.
   * Am schnellsten: ein Test-Release, z. B. `3.0.0` mit dem echten Installer, als Beispiel-JSON schicken.
   * Oder veröffentlichen, dann prüft die Produktionsprüfung es automatisch.
2. **Authenticode:** Das Schema verlangt `authenticode.verified: true`. Die heutigen CI-Builds sind **nicht
   code-signiert.** Ohne OV/EV-Zertifikat kann der Release Manager also kein gültiges Release anlegen. Bitte klärt:
   * Gibt es ein Zertifikat?
   * Prüft der Release Manager die Signatur beim Upload?
3. **Portables ZIP (optional, für Updates in der App):** Soll die App ohne Browser aktualisieren und zurückrollen
   können, braucht das signierte Payload zusätzlich ein portables Paket, z. B.
   `portable: {download_url, sha256, size_bytes}`. Das wäre eine Erweiterung eures Schemas. Ohne sie bleibt es beim
   Hinweis auf die Downloadseite.
4. **Wo der Release-Signierschlüssel liegt:** Der Server veröffentlicht den öffentlichen Schlüssel und liefert
   signierte Payloads aus. Liegt der private Schlüssel auf dem VPS, kann ein kompromittierter VPS Releases fälschen.
   Die App fängt das ab: Sie installiert nichts automatisch, und der Nutzer lädt den Installer selbst herunter, mit
   SHA-256 und Authenticode. Trotzdem empfehlen wir, offline oder in CI zu signieren. Bitte bestätigt euer Vorgehen.
   **Bitte den Schlüssel nicht wechseln**, ohne vorher den neuen öffentlichen Schlüssel in einen Build zu geben.
5. **Download-Hosts:** Liegt `download_url` nicht auf `akeno.online` selbst, sondern auf einer Subdomain, nennt die
   Hosts. Sie kommen in die Variable `AKENO_DOWNLOAD_HOSTS`.
6. Optional: `latest_client_version` in `/system/version` auf die veröffentlichte Version setzen. Die App zeigt den
   Wert nur an.

### GitHub-Variablen für die Updateverwaltung

Für den Release Manager ist **keine Variable nötig**, die Standardwerte sind eingebaut:

| Variable | Standard | Wann setzen |
|---|---|---|
| `AKENO_RELEASE_API` | `https://api.akeno.online/api/v1` | nur bei einer anderen API-Adresse |
| `AKENO_RELEASE_PAGE` | `https://akeno.online/downloads/latest` | nur bei einer anderen Downloadseite |
| `AKENO_DOWNLOAD_HOSTS` | `akeno.online` | wenn Downloads auf einer Subdomain liegen (Punkt 5) |
| `AKENO_RELEASE_PUBLIC_KEYS` | eingebaut: `d6139235b220a4aa:bpe9acmephsoZSYzy12R2XFy_lJZ_69MiV31ekehcdo` | nur beim Schlüsselwechsel |
| `AKENO_UPDATE_SOURCE` | leer, also Release Manager | `github` oder `akeno` nur für die alten Wege |
| `AKENO_RELEASE_BASE` | — | **nicht setzen.** Er gehört zum alten Vorschlag mit statischen Kanal-Dateien, den die Produktion nicht anbietet |

## C. Nicht Teil dieser Übergabe

* Kein öffentlicher Release, kein signiertes Manifest, kein Upload. Das braucht die Freigabe des Inhabers.
* Kein Merge nach `main`.
