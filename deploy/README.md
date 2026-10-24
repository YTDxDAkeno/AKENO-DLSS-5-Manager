# AKENO licensing server: VPS deployment

The VPS runs the **license server only**: accounts and passwords, activation codes, licenses, devices, signed
offline licenses, the account website and the admin website, and their audit log. Everything else of the AKENO
Gaming Suite runs on the Windows PC: DLSS 5 mods, the FPS Booster, benchmarks, Desktop Studio, community packages
and sync. App updates come from the public release repository on GitHub. No game files, DLLs, wallpapers or
installers are ever uploaded here, and the VPS needs no access to GitHub (the source repository is private).

The server knows one generic entitlement, `akeno-suite`. Which modules a license unlocks is decided by the app, so
a new AKENO version never needs a server change. The `/api/v1` licensing API only grows (new fields, never
changed ones).

## Kurzanleitung (Deutsch)

1. Server-Paket auf den VPS bringen (der VPS braucht keinen GitHub-Zugang): auf deinem PC im Quellcode
   `deploy/bundle.sh` ausführen (oder das Artefakt `akeno-licensing-server-bundle` aus GitHub Actions laden), dann
   `scp dist/akeno-licensing-*.tar.gz* user@vps:` und auf dem VPS
   `sha256sum -c akeno-licensing-*.tar.gz.sha256 && sudo mkdir -p /opt/akeno-licensing &&`
   `sudo tar -xzf akeno-licensing-*.tar.gz -C /opt/akeno-licensing --strip-components=1`, dann
   `cd /opt/akeno-licensing/deploy`.
2. `./install.sh` ausführen. Das Skript erzeugt die Geheimnisse in `.env`, fragt nach deinen Adressen, baut und
   startet die Container und erzeugt den Lizenz-Signaturschlüssel. Am Ende gibt es den öffentlichen Schlüssel aus.
3. Die beiden Seiten aus `Caddyfile.example` in deine bestehende Caddy-Konfiguration übernehmen, Platzhalter durch
   deine Hostnamen ersetzen und Caddy neu laden. Gibt es noch keinen Reverse Proxy:
   `docker compose --profile caddy up -d`.
4. `./create-admin.sh deine@adresse` legt einmalig den Super Admin an (das Passwort wird im Terminal abgefragt).
5. Auf der Admin-Website anmelden, die Zwei-Faktor-Anmeldung einrichten und Aktivierungscodes erzeugen.
6. In GitHub unter *Settings → Secrets and variables → Actions → Variables* `AKENO_LICENSE_SERVER_URL` (deine
   öffentliche Adresse) und `AKENO_LICENSE_PUBLIC_KEYS` (die JSON-Ausgabe von Schritt 2) setzen. Der nächste
   Windows-Build enthält dann Server und Schlüssel.

## Services

| Service | Image | Job | Port (127.0.0.1) |
|---------|-------|-----|------------------|
| `akeno-api` | built from `../server` | JSON API for the app (`/api/v1`), migrations on start, holds the signing keys | 8410 |
| `akeno-web` | same | landing page and account website | 8411 |
| `akeno-admin` | same | admin website (its own host name, can be IP-restricted) | 8412 |
| `akeno-database` | `postgres:16-alpine` | PostgreSQL, never exposed | — |
| `akeno-backup` | `postgres:16-alpine` | daily `pg_dump` + signing keys, keeps 14 | — |
| `akeno-caddy` | `caddy:2.8-alpine` | optional (`--profile caddy`), only without an existing proxy | 80/443 |

It coexists with other projects on the same VPS: the project name `akeno-licensing` prefixes every container,
network and volume; there are no fixed container names; ports are configurable and bound to 127.0.0.1; the
database has its own volume and is reachable only inside the project network. Nothing touches other containers,
databases or an existing Caddy configuration.

## Reverse proxy

* **Caddy on the host** (systemd): copy the two sites of `Caddyfile.example`, replace the host names, `caddy reload`.
* **Caddy in Docker**: start AKENO with the override so its services join the proxy's network, and use
  `akeno-api:8000`, `akeno-web:8000`, `akeno-admin:8000` as upstreams:
  `AKENO_PROXY_NETWORK=<network> docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d`
* **No proxy yet**: set `AKENO_PUBLIC_HOST` and `AKENO_ADMIN_HOST` in `.env`, then
  `docker compose --profile caddy up -d` (Caddy gets certificates automatically; ports 80 and 443 must be free).

The public host routes `/api/*` to `akeno-api` and everything else to `akeno-web`; the admin host routes
everything to `akeno-admin`. Restricting the admin host to your own IP address is a good idea (see the comment in
`Caddyfile.example`).

## Configuration (`.env`)

`install.sh` creates `.env` from `.env.example` (mode 600) and generates `POSTGRES_PASSWORD`,
`AKENO_TOKEN_SECRET` and `AKENO_CODE_PEPPER`. You set:

| Variable | Meaning |
|----------|---------|
| `AKENO_PUBLIC_URL` | `https://` address of the account site, used in e-mail links |
| `AKENO_ADMIN_URL` | `https://` address of the admin site (links between the two sites) |
| `AKENO_MAIL_BACKEND` | `smtp` for real e-mail; `file` (default) writes `.eml` files into the `akeno-mail` volume |
| `AKENO_SMTP_*`, `AKENO_MAIL_FROM` | your mail provider's SMTP host, port, user, password and sender |
| `AKENO_BIND`, `AKENO_*_PORT` | where the services listen for the proxy |
| `BACKUP_INTERVAL_HOURS`, `BACKUP_KEEP` | backup schedule |

**Never change `AKENO_CODE_PEPPER`** after codes exist: unused codes are stored only as HMAC verifiers with it.
Changing `AKENO_TOKEN_SECRET` signs everybody out (licenses keep working offline). Run-time settings (registration,
device limits, offline grace period, check interval, maintenance mode, app versions) are changed on the admin
website under *Settings*.

## Signing keys

Licenses are signed with Ed25519. The private key is created by `install.sh` (`akeno-server keys init`) inside the
`akeno-keys` volume, which only `akeno-api` mounts; the database and the API publish only the public key
(`/api/v1/system/keys`). Put the public key into the Windows build (repository variables above, or
`tools/configure-licensing.py`). To rotate: `docker compose exec akeno-api akeno-server keys rotate`. The new key is
endorsed by the old one, so installed apps accept it automatically, and old lifetime certificates stay valid.

The backups contain the private key: keep `deploy/backups/` (mode 700) and any copies private.

## Operations

| Task | Command |
|------|---------|
| First installation | `./install.sh` |
| First super admin (once) | `./create-admin.sh you@example.org` |
| Update from your PC | `deploy/push-to-vps.sh user@vps /opt/akeno-licensing` (builds the bundle, copies it over SSH, runs `update.sh --bundle` there) |
| Update with a bundle on the VPS | `./update.sh --bundle ../incoming/akeno-licensing-<v>-<commit>.tar.gz --sha256 <hash>` (backs up, checks the hash and the contents, keeps `.env`, `backups/` and the keys, rebuilds; migrations run on start) |
| Back to the previous server | `./update.sh --rollback` |
| Backup now | `./backup.sh` (copies the newest dump and key archive to `./backups`) |
| Restore | `./restore.sh backups/akeno-db-<stamp>.dump backups/akeno-keys-<stamp>.tar.gz` |
| Status | `docker compose ps`, `docker compose exec akeno-api akeno-server check` |
| Logs | `docker compose logs -f akeno-api` |
| Lost authenticator (staff) | `docker compose exec akeno-api akeno-server reset-2fa --email <address>` |
| Retention cleanup | `docker compose exec akeno-api akeno-server cleanup` |

Behind a TLS-inspecting proxy, give the build your CA:
`docker build --secret id=pip_ca,src=/path/ca.crt -t akeno-licensing:local ../server`, then
`./install.sh --no-build`.

## Updates without GitHub access on the VPS

The source repository is private, and the VPS needs no credentials for it:
- **Bundles:** `deploy/bundle.sh` (on your PC, or the CI artifact `akeno-licensing-server-bundle`) packs only the committed `server/` and `deploy/` folders, with a SHA-256 file. No `.env`, keys, backups, Windows app or history are in it.
- **Copying:** `push-to-vps.sh` copies the bundle over your normal SSH access.
- **Checks on the VPS:** `update.sh --bundle` refuses a bundle whose hash does not match, or that contains links, absolute paths, `..` or anything outside `server/` and `deploy/`. It keeps the previous server for `--rollback`.
- **When an update is needed:** only for changes to the license server itself. New Windows versions never need a server update (the `/api/v1` API only grows; the entitlement `akeno-suite` is generic).

If you prefer the VPS to fetch updates itself, give it a **read-only deploy key** for this repository only. Never use a personal token or an account password.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/akeno_deploy -N "" -C "akeno-licensing VPS (read-only)"
cat ~/.ssh/akeno_deploy.pub     # GitHub: repository -> Settings -> Deploy keys -> Add deploy key, write access OFF
cat >> ~/.ssh/config <<'CFG'
Host github-akeno
    HostName github.com
    User git
    IdentityFile ~/.ssh/akeno_deploy
    IdentitiesOnly yes
CFG
git clone --filter=blob:none --sparse git@github-akeno:YTDxDAkeno/AKENO-DLSS-5-Manager.git /opt/akeno-licensing
git -C /opt/akeno-licensing sparse-checkout set server deploy
cd /opt/akeno-licensing/deploy && ./update.sh --git
```

A deploy key can read this one repository only, cannot push, and can be revoked at any time on the same settings page.

## What was tested

On this repository's CI and in a local Docker run (Docker 29, Compose 5): `install.sh` from an empty `.env`,
all five containers healthy, the super admin bootstrap (and its refusal the second time), the admin website with
two-factor enrolment, code generation, a user registering and redeeming on the account site, the Windows app's
licensing code signing in and redeeming against `akeno-api`, `backup.sh`, `restore.sh` (database and keys), and a
rebuild with recreated containers keeping all data. A real domain, real SMTP and Let's Encrypt certificates were
not part of that test: they depend on your VPS.
