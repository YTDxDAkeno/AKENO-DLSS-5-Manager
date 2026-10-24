# Übergabe an Codex: was auf dem VPS für AKENO 3.0 noch zu tun ist

Stand: 2026-10-24. Branch `claude/clever-fermi-xykqbw` (auf PR #15 → #14 → #13). Auf GitHub ist nichts gemergt,
nichts veröffentlicht und auf dem VPS nichts verändert.

Diese Datei ist die Reihenfolge. Die Details stehen in:

| Datei | Inhalt |
|---|---|
| `CODEX_ACCOUNTS_HANDOVER.md` | Konto-Änderungen: Zweck jedes Hunks, Konfliktrisiko, Vertrag, Konfiguration, Abnahme |
| `accounts-server.patch` | Patch für `server/akeno_server/` (9 Dateien). SHA-256 `d077080a16d66d672f367c1698ba3901c2fa17705a9e784e3e16035666095f7f` |
| `CODEX_RELEASE_MANAGER.md` | Was der Windows-Updater vom VPS braucht: Dateien, Format, Signatur, Abnahmetests |
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
3. **Kein privater Schlüssel auf den VPS und keiner in die App:**
   * Der Release-Signierschlüssel (Ed25519-Seed) liegt nur als GitHub-Secret `AKENO_RELEASE_SIGNING_KEY` oder
     offline vor.
   * Die Lizenz-Signierschlüssel bleiben dort, wo sie heute sind: auf dem Lizenzserver.
   * Die App enthält nur öffentliche Schlüssel. Der Windows-Build prüft das bei jedem Lauf
     (`tools/check_build_config.py`).

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

### Was die App heute erwartet

Die App braucht keine neue API, sondern statische, signierte Dateien über HTTPS auf `akeno.online`:

```
{release_base}/channels/stable.json     # {"format": "akeno-release-channel", "token": "<JWS>", ...}
{release_base}/channels/beta.json
<URLs aus dem signierten Manifest>       # portable ZIP (Pflicht), Setup-EXE, Quell-ZIP, SHA256SUMS.txt
```

* Erzeugt werden die Kanal-Dateien mit `tools/release_manifest.py sign --base-url …`, siehe
  `CODEX_RELEASE_MANAGER.md` §4. Der Release-Prüfschlüssel in der App ist `d6139235b220a4aa`.
* Ein Manifest mit einem anderen Schlüssel, einer falschen SHA-256 oder einer älteren Version installiert die App
  nicht.
* Ohne Bestätigung durch den Nutzer installiert sie ebenfalls nichts.

### Was wir von euch brauchen, bevor die App umgestellt wird

Im Repository gibt es keinen API-Vertrag des Release Managers. Wir haben deshalb keinen Endpunkt erfunden. Die
Lese-Prüfung liest seit diesem Branch das öffentliche OpenAPI-Dokument der Produktion (`tools/api_contract.py`) und
listet jeden Pfad, der mit Releases oder Updates zu tun hat. Ergebnis: siehe `PRODUCTION_STATUS.md`.

Bitte bestätigt oder liefert:

1. **`release_base`**, z. B. `https://downloads.akeno.online/releases`.
2. **Liefert der Release Manager genau die Dateien oben aus**, also das signierte Kanal-Dokument unverändert, byte
   für byte?
   * Ja: Für die App ist nichts weiter zu tun.
   * Nein, weil er ein eigenes Format oder eigene Endpunkte hat (z. B. `GET /api/v1/releases/latest`): Schickt das
     OpenAPI-Schema oder ein Beispiel-JSON. Wir bauen dann einen Adapter. Die Release-Signatur muss erhalten bleiben,
     denn ohne sie installiert die App nichts automatisch.
3. **Wo signiert wird:** Außerhalb des VPS, in GitHub Actions oder offline. Der Release Manager lädt nur bereits
   signierte Kanal-Dokumente und Dateien hoch und veröffentlicht sie, indem er die Kanal-Datei atomar umbenennt.
   Er darf den Signierschlüssel nicht besitzen.
4. **`Cache-Control: no-cache`** auf `channels/*.json`, und kein Redirect auf fremde Domains.
5. **Admin-Felder `latest_client_version` und `min_client_version`** in `/system/version`: Die App liest sie heute nur
   zur Anzeige. Wenn der Release Manager sie setzt, sollen sie zur veröffentlichten Kanal-Version passen.

### Danach auf GitHub (Repository-Variablen, nichts Geheimes)

```
AKENO_UPDATE_SOURCE=akeno
AKENO_RELEASE_BASE=<euer release_base>
AKENO_RELEASE_PAGE=https://akeno.online/download
AKENO_DOWNLOAD_HOSTS=<weitere Hosts unter akeno.online, falls die Dateien woanders liegen>
```

Die Lese-Prüfung holt dann beide Kanäle und prüft sie mit dem eingebauten Schlüssel.

Abnahme: `CODEX_RELEASE_MANAGER.md` §7. Erst eine Beta, dann die Negativtests: Ein verändertes Byte, ein fremder
Schlüssel und ein älteres Manifest müssen jeweils abgelehnt werden. Zuletzt den Rollback testen.

## C. Nicht Teil dieser Übergabe

* Kein öffentlicher Release, kein signiertes Manifest, kein Upload. Das braucht die Freigabe des Inhabers.
* Kein Merge nach `main`.
