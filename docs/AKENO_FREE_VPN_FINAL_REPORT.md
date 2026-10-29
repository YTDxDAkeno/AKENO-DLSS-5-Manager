# AKENO Gaming Suite 3.2.0 — kostenloses Ein-Klick-VPN (VPN Gate über OpenVPN): Abschlussbericht

Stand: 29.10.2026. Branch `claude/akeno-free-vpn` (Fortsetzung von `claude/confident-goodall-i10aop` @ `ba50de4`).
**Nichts wurde veröffentlicht, signiert, gemergt oder deployt.** Kein bezahlter Server, keine Produktionsänderung,
kein Lizenzserver-Eingriff. Laufender Arbeitsstand: `docs/AKENO_FREE_VPN_PROGRESS.md`.

> **Ergebnis:** Technisch funktioniert der Ein-Klick-Ablauf auf echtem Windows mit echten VPN-Gate-Servern
> (26/26 reale Prüfungen). **Veröffentlichung: NO-GO bis zu deiner Entscheidung zur Nutzungserlaubnis** (Punkt 9 und
> 14) und den üblichen Freigabeschritten (Offline-Signatur, Tests auf deinem PC).

## Kurzantworten

| # | Frage | Antwort |
|---|---|---|
| 1 | Funktioniert der VPN-Gate-Serverabruf? | **Ja.** Offizielle Liste `https://www.vpngate.net/api/iphone/` über HTTPS, ohne Weiterleitungen, größenbegrenzt, ein stiller zweiter Versuch, 10-min-Cache ohne Profile, höchstens alle 60 s. In CI 97–99 Server je Abruf. Ein einzelner 30-s-Timeout der Liste wurde beobachtet und wird ehrlich gemeldet. |
| 2 | Funktioniert die automatische Länderauswahl? | **Ja.** Kandidaten nur aus genau dem gewählten Land (nie ein anderes), Qualität aus Bandbreite/Sitzungen/Ping/Uptime, UDP bevorzugt, instabile Server (< 10 min Uptime) und ohne Bandbreite ausgeschlossen; ein Land ohne Server → „Nicht verfügbar“ (CI: TR sofort `unavailable`). |
| 3 | Funktioniert das automatische Verbinden unter Windows? | **Ja.** Lauf [40814425437](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40814425437): nach einem abgelehnten Server verbunden mit `60.141.30.170` (Japan) in ~60 s inkl. Prüfungen. |
| 4 | Wurde eine echte öffentliche VPN-IP gemessen? | **Ja.** Vorher `52.159.244.74` (Runner), danach **`60.141.30.170`**, gemessen durch den Tunnel (an AKENOs Adapter gebunden) **und** mit einer normalen ungebundenen HTTPS-Anfrage. Frühere Läufe: `113.156.134.59`, `219.100.37.238/.245` u. a. |
| 5 | Wurde das Land unabhängig überprüft? | **Ja.** ipinfo, country.is und ifconfig.co bestätigten **JP** (mindestens 2 Bestätigungen und mehr Bestätigungen als Widersprüche sind Pflicht). Gegenprobe: KR-Server als „US“ ausgegeben → zweimal `country_mismatch`, **nie** „Verbunden“. |
| 6 | Funktionieren Trennen und Recovery? | **Ja.** Trennen über OpenVPNs Exit-Event; danach kein AKENO-OpenVPN, **0** AKENO-Routen, normale IP zurück. Server-Ausfall (Firewall-Sperre) → Wechsel auf anderen JP-Server; getötetes OpenVPN sofort erkannt; abgestürzter Helfer nimmt OpenVPN mit (Job-Objekt); Wiederherstellung räumt Routen auf. |
| 7 | Sind IPv4, IPv6 und DNS abgesichert? | **IPv4:** Full-Tunnel (`0/1`, `128/1` auf `AKENO-FreeVPN`), normaler Verkehr nachweislich über das VPN. **DNS:** Tunnel-DNS 1.1.1.1/9.9.9.9 durch den Tunnel, Server-DNS-Pushes verworfen, DNS außerhalb des Tunnels durch OpenVPNs `block-outside-dns` gesperrt und **nativ gemessen** (`dns_verified: True`). **IPv6:** `block-ipv6` + IPv6-Verbindungstest; auf dem CI-Runner gibt es **kein IPv6**, der Test ist dort trivial bestanden — auf einem IPv6-Anschluss noch nicht gemessen. **Kein Kill-Switch** (siehe 12). |
| 8 | Welche Länder waren beim Test tatsächlich verfügbar? | Lauf 40814425437: JP 42, KR 12, TH 3, US 2, RU 2, **CA 1**, IT 1, LV 1 (nutzbare Server). Ziel-Länder: **US 2, CA 1, JP 42; TR, DE, NL, FR, GB 0**. Andere Abrufe: US 3–7, GB 1. Die Verfügbarkeit wechselt stündlich. |
| 9 | Welche Nutzungs- und Datenschutzbedingungen gelten? | Siehe Abschnitt „Nutzungsrechte und Datenschutz“. Kurz: eigener Client aus der CSV-Liste ausdrücklich vorgesehen; Server als „Academic Use Only“ gekennzeichnet; Verbindungsprotokolle ≥ 3 Monate, Paket-Header ≥ 2 Wochen, Herausgabe an Behörden. **Kommerzieller Kontext ungeklärt.** |
| 10 | Welche OpenVPN-Komponenten, dürfen sie verteilt werden? | Offizielles **OpenVPN 2.7.8** (`OpenVPN-2.7.8-I002-amd64.msi`, SHA-256 `33104dd0…fe9f`, Authenticode „OpenVPN Inc.“), installiert nur `OpenVPN,Drivers,Drivers.TAPWindows6`. **AKENO verteilt OpenVPN nicht**: es lädt nach Zustimmung den offiziellen Installer von openvpn.org (GPLv2-Programm; TAP-Treiber von OpenVPN Inc. signiert). Der „OpenVPNServiceInteractive“ kommt mit dem Programm-Feature (CI-verifiziert) und wird von AKENO nicht genutzt. |
| 11 | Welche Tests sind grün? | Abschnitt „Tests“. Reale Windows-E2E 26/26; Unit-/UI-Tests (Sanitizer 48, Provider 8, Sitzung 17, Komponente 10, Helfer 40, UI 8, DNS-Probe 30). Finale CI-Links in Abschnitt „CI auf dem finalen Commit“. |
| 12 | Welche Tests fehlen noch? | Echter IPv6-Leck-Test auf IPv6-Anschluss; WLAN-Wechsel, Standby/Resume, Windows-Neustart mit aktivem VPN; UAC-Dialog auf normalem Benutzerkonto; Normales Heimnetz (CI ist ein Azure-Rechenzentrum); optionaler Kill-Switch (nicht implementiert); ARM64-Windows (Komponente nur x64). |
| 13 | Wo ist der fertige private Installer? | Im Artefakt `AKENO-Gaming-Suite-windows-x64` des `build.yml`-Laufs auf dem finalen Commit (Link und SHA-256 im Abschnitt „CI auf dem finalen Commit“). |
| 14 | Was verhindert noch eine Freigabe? | (a) **Nutzungserlaubnis** von VPN Gate für ein kommerzielles Produkt bzw. deine ausdrückliche Entscheidung; (b) deine Tests auf dem eigenen PC (Abschnitt „Test auf deinem PC“); (c) Offline-Signatur Sequenz 4 nach Freigabe; (d) offene Messungen aus 12. |

## Was der Nutzer sieht

AKENO Network → VPN → **Kostenloses VPN** (ganz oben; AKENO-eigene Server und eigene WireGuard-Profile darunter
eingeklappt): Länderkacheln (USA, Türkei, Deutschland, Japan, Niederlande, Frankreich, Kanada, Großbritannien und
alle weiteren gelisteten Länder) mit „Verfügbar · n Server · ~x ms laut Liste“ oder „Nicht verfügbar“, Suche,
Favoriten, **Verbinden**, **Trennen**, **Server wechseln**, **Liste aktualisieren**, **Wiederherstellen**,
**Datenschutz**. Status: öffentliche VPN-IP, verifiziertes Land mit Anzahl Quellen, Server (Name · TCP/UDP), Ping
(gemessen durch den Tunnel, sonst „—“), Verbindungsdauer, Anbieter „VPN Gate · Community“. Vor der ersten Verbindung
muss der Datenschutzhinweis bestätigt werden; die Komponente wird erst nach eigener Zustimmung installiert (eine
UAC-Abfrage). Deutsch und Englisch.

## Sicherheitsdesign

* **Profile sind nicht vertrauenswürdig** (`akeno/network/openvpn_profile.py`): gelesen werden nur proto, genau ein
  `remote` (muss die gelistete öffentliche IP sein), Cipher/Digest aus Allowlists, `<ca>`/`<cert>`/`<key>` (echt
  geparst und neu serialisiert), optional tls-auth/tls-crypt. Abgelehnt: Skripte, Plugins, Dateipfade, Management,
  Proxys, Kompression, Routen, `setenv`, unbekannte Direktiven/Inline-Blöcke, Quoting, Hostnamen, fehlende CA.
* **Eigene Konfiguration:** feste Direktiven, `verify-x509-name opengw.net name` (alle VPN-Gate-Server zeigen ein
  Let's-Encrypt-Zertifikat für `opengw.net`; die CA allein würde jedes Let's-Encrypt-Zertifikat akzeptieren),
  `script-security 1` ohne Skripte, Full-Tunnel, `block-ipv6`, `block-outside-dns`, eigenes DNS, Pull-Filter gegen
  Routen/IPv6/DNS/Domains/Proxys/Umgebungsvariablen vom Server, kein `mute`.
* **Prozess:** nur `openvpn.exe` der signierten Installation, feste Argumente (`--config`, `--log`, `--service`),
  Konfiguration in einem neuen Administratoren/SYSTEM-Ordner mit hoher Integritätsstufe, Helfer im
  Kill-on-close-Job, Stopp über Exit-Event, danach Aufräumen der Routen nur auf AKENOs Adapter und zu benutzten
  Servern.
* **Verifikation vor „Verbunden“:** Adapter oben, Internet-Routen auf dem Adapter, Tunnel-DNS konfiguriert, IPv6
  verlässt den PC nicht, Exit-IP ≠ normale IP, ≥ 2 Geo-Bestätigungen, DNS-Leckmessung nativ. Leck → Sitzung endet;
  falsches Land → nächster Server; nie ein anderes Land.
* **Getrennt von LAN Play:** eigene Stores, eigener Adapter, keine Änderung an LAN-Firewallregeln oder der
  Relay-only-WFP-Sperre; LAN Play und jedes VPN schließen sich gegenseitig aus.
* **Keine Verläufe:** gespeichert werden nur Status, Zustimmung (Version) und Favoriten.

## Nutzungsrechte und Datenschutz (Quellen: Seiten von vpngate.net, gelesen durch CI)

* „Instead you can use CSV List to make your own VPN Gate client app.“ (Startseite) — ein eigener Client auf Basis der
  CSV-Liste ist vom Projekt vorgesehen.
* „Using the VPN Server List of VPN Gate Service as the IP Blocking List of your country's Censorship Firewall is
  prohibited by us.“
* Betreiberfeld der Universitätsserver: „Daiyuu Nobori_ Japan. **Academic Use Only.**“ FAQ: akademisches
  Forschungsprojekt der Universität Tsukuba; „Japanese national universities have a general rule to prohibits any
  profit activities.“
* Anti-Abuse-Policy: Verbindungsprotokolle **≥ 3 Monate** (zentrale Logserver der SoftEther Corporation), jeder
  Freiwilligen-Server zeichnet **Paket-Header aller Nutzer ≥ 2 Wochen** auf, Herausgabe an Polizei,
  Staatsanwaltschaften, Anwälte oder Gerichte mit gesetzlicher Befugnis.
* **Bewertung:** AKENO verlangt für das kostenlose VPN kein Geld und keinen Aufpreis; es ist aber Teil eines
  lizenzpflichtigen Produkts (Netzwerkfunktionen setzen eine aktive Lizenz voraus). Ob das mit „Academic Use Only“
  vereinbar ist, beantworten die Seiten nicht. **Empfehlung:** schriftliche Anfrage an das VPN-Gate-Projekt vor
  einer öffentlichen Veröffentlichung; alternativ das kostenlose VPN ohne Lizenzpflicht anbieten. Private Weitergabe
  an Freunde ändert an dieser Frage nichts.
* OpenVPN (GPLv2) wird nicht mit AKENO verteilt, sondern offiziell von openvpn.org geladen; AKENO enthält keinen
  OpenVPN-Code.

## Tests

### Reale Windows-E2E (Produktionscode, echte VPN-Gate-Server) — Lauf [40814425437](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40814425437) auf `a81586d`: **26/26 grün**

Zustimmungs-Installation (gepinnte SHA-256, Authenticode online) · Komponente bereit · keine GUI, kein
Auto-Connect-Dienst · Liste gelesen (97 Server) · Land ohne Server (TR) → „nicht verfügbar“, kein anderes Land ·
JP verbunden · IP geändert · Land von 3 Quellen bestätigt · DNS nativ verifiziert, IPv6 nicht ausgehend · normaler
Verkehr über VPN · Routen und DNS auf dem AKENO-Adapter · Namensauflösung über Tunnel-DNS · „Server wechseln“ auf
anderen JP-Server · Server-Ausfall erkannt und gewechselt · getötetes OpenVPN sofort erkannt · Trennen · keine
Prozesse/Routen übrig · normale IP zurück · Helfer-Absturz nimmt OpenVPN mit · Recovery räumt auf · IP zurück ·
falsches Exit-Land nie „Verbunden“ · nichts übrig nach abgelehnten Servern.

Vorheriger Lauf [40813800435](https://github.com/YTDxDAkeno/AKENO-DLSS-5-Manager/actions/runs/40813800435) (22/26)
fand liegengebliebene Routen nach erzwungenem OpenVPN-Ende → Behebung `a81586d`.

### Negativtests (Unit/Integration)

Ungültige CSV, manipulierte Konfiguration und gefährliche Direktiven (33 Fälle), falsche Server-IP/Hostname,
fehlende CA, Quoting/Injection, ungültige Pfade/Namen in der Konfiguration, veraltete/instabile Server, nicht
erreichbare Server (Failover), fehlende Komponente, abgelehnte Zustimmung, abgelaufene/fremde Helfer-Anfragen,
unterbrochene Liste (Retry, dann Fehler), Land ohne Server, nicht übereinstimmendes Exit-Land, DNS-/IPv6-Leck beendet
die Sitzung, OpenVPN-Absturz, Serverwechsel, Recovery nur nach entferntem Prozess und Routen.

## CI auf dem finalen Commit

wird nach dem letzten Code-Commit eingetragen.

## Test auf deinem PC (Windows 10/11 x64, normales Konto)

1. Artefakt `AKENO-Gaming-Suite-windows-x64` des finalen Laufs installieren (oder Portable entpacken).
2. AKENO → ONLINE → AKENO Network → VPN einschalten → Tab VPN → „Kostenloses VPN“.
3. „Liste aktualisieren“ → Japan (oder USA, falls verfügbar) wählen → „Verbinden“ → Datenschutzhinweis lesen und
   bestätigen → „VPN-Komponente installieren…“ bestätigen → UAC bestätigen → erneut „Verbinden“ → UAC bestätigen.
4. Erwartet nach 10–60 s: „Verbunden“, eine japanische IP, „Japan (2–3 Quellen)“, Server, Ping. Gegenprobe im Browser:
   https://ipinfo.io zeigt dieselbe IP und JP.
5. „Server wechseln“ → andere japanische IP oder derselbe Pool; „Trennen“ → normale IP (ipinfo.io erneut).
6. Optional E2E auf deinem PC (Administrator-PowerShell im Repository):
   `python tools\free_vpn_windows_e2e.py --skip-install --countries JP,US --report free-vpn-report.json`
7. Bitte melden: WLAN-Wechsel/Standby während der Verbindung, IPv6-Anschluss (https://test-ipv6.com darf mit VPN
   keine IPv6-Adresse zeigen).
