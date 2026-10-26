# AKENO 3.1 – Systemwerkzeuge (System Doctor, Smart Cleaner, Network Lab, Universal Mod Scanner, Security Center)

Technische Beschreibung für Entwicklung und Review. Stand: Branch `claude/adoring-shannon-2d4qb4`.

## 1. Grundsätze

- **Messung vor Behauptung.** Jede Aussage ist ein `Finding` mit Belegen der Art *gemessen*, *mögliche Ursache*
  oder *Korrelation (gleiche Zeit, nicht bewiesen)*. Fehlt ein Wert, steht „nicht verfügbar“ samt Grund da; es
  werden keine Sensordaten erfunden.
- **Nichts ohne Bestätigung.** Lesen ist immer erlaubt; jede Änderung (Autostart, Bereinigung, DNS, Reparatur,
  Pausieren von Programmen, Defender-Scan) läuft nur nach Klick und ggf. Bestätigungsdialog, wird im Journal
  (`system.sqlite3`) vorgemerkt, ausgeführt und mit Ergebnis abgeschlossen. Was umkehrbar ist, hat ein „Rückgängig“.
- **Administratorrechte nur bei Bedarf**, über den normalen UAC-Dialog (`ShellExecuteExW runas`), nie still.
- **Lokal.** Ergebnisse bleiben auf dem PC (`system.sqlite3`, getrennt von `akeno.sqlite3` und Cloud Sync).
  Nichts davon geht an den VPS; keine Telemetrie.
- **Nicht blockierend.** Lange Arbeit läuft in `ui/workers.py` mit `TaskContext` (Abbruch, Zeitlimit,
  Fortschritt, Mengengrenzen).

## 2. Architektur

```
akeno/system/                  Kern ohne Qt
  model.py                     Finding, Evidence, Reading, Report, Level
  tasks.py                     TaskContext, Progress, Cancelled/TimedOut/LimitReached
  commands.py                  Windows-Werkzeuge ohne Shell; PowerShell -EncodedCommand; UAC; open_uri (nur
                               ms-settings: und windowsdefender:)
  store.py                     system.sqlite3: kv, Historie, Aktionsjournal (planned → done/failed/undone)
  fs.py                        sicheres Durchlaufen (keine Links/Junctions/Reparse-Punkte), Containment,
                               Datei-Identität, Sperrprüfung, Papierkorb (SHFileOperationW FOF_ALLOWUNDO)
  signatures.py                Authenticode eingebettet + Katalog
  lnk.py                       Shell-Link-Parser (nur lesen)
  suite.py                     SystemTools (lazy an AppServices.system_tools)
  doctor/                      System Doctor
  cleaner/                     Smart Cleaner
  network/                     Network Lab
  mods/                        Universal Mod Scanner
  securitycenter/              Security Center
akeno/recovery.py              Wiederherstellung ohne aktive Lizenz (nur Rückgängig)
akeno/ui/system_ui.py          TabbedPage, TaskBar, FindingView/FindingList, ReadingTile, Sparkline, Tabellen
akeno/ui/pages/{doctor,cleaner,network,security,installed}.py
akeno/ui/recovery_dialog.py    „Spieldateien wiederherstellen“ auf dem Willkommensbildschirm
tools/system_check.py          echte Provider auf dem Windows-CI-Runner (nur lesend, EICAR-Test optional)
```

Jedes Modul trennt **Datenerfassung** (Provider: echte Windows-Implementierung + Stand-in für Tests),
**Diagnose** (reine Funktionen auf Daten → Findings), **Empfehlungen** (Text + Link in andere Module),
**UI**, **bestätigte Aktionen** (Journal + Undo) und **Protokoll** (Historie).

Querverbindungen laufen über `Finding.links` (`Link(module, label, target)`); jede Seite versteht
`show_target(target)`, z. B. Doctor → Smart Cleaner (`scan`), Installed Mods → Security Center (`file:<Pfad>`),
Security Center → Windows-Sicherheit (`uri:history`).

## 3. Module

### System Doctor
Dashboard (CPU, RAM, Datenträger, GPU über `nvidia-smi` nur wenn vorhanden, Temperaturen nur aus echten Quellen),
optionaler transparenter Gesundheitsindikator (Abzüge je Problem sichtbar), Slow PC Detective (gemessen /
mögliche Ursache / Korrelation), Startup Analyzer (Task-Manager-Mechanismus `StartupApproved`, Deaktivieren und
Wiederherstellen nach Bestätigung, Sicherheitssoftware geschützt), Crash Detective (Ereignisprotokoll, Minidumps,
Bugcheck-Codes), Hardware Health (SMART/NVMe über Storage-Cmdlets, erweitert nur elevated nach Bestätigung),
geführte Reparatur (DISM/SFC nur bei Bedarf, sichtbares Konsolenfenster, Ergebnis aus dem Exitcode).

### Smart Cleaner
Kategorien mit exakten Wurzelordnern, Mindestalter, Grund, Risiko, Nebenwirkung. Ablauf: Scan → Ergebnis →
Auswahl → Vorschau der exakten Dateien → Bestätigung → Fortschritt → Bericht (freier Platz vorher/nachher). Vor
jeder Löschung wird erneut geprüft: innerhalb der bestätigten Wurzel, unverändert seit dem Scan, kein Link, nicht
gesperrt; Windows, Programme, Prefetch und geschützte Namen werden von einer letzten Pfadsperre abgewiesen.
Standard ist der Papierkorb; endgültiges Löschen nur auf ausdrückliche Wahl. Browser: nur Cache-Ordner, nie
Cookies, Logins, Sitzungen, Profile. Shader-Caches nie vorausgewählt, extra Bestätigung. Smart Maintenance: aus
per Default, nur ein lesender Check, nie eine Löschung, kein Autostart, kein Admin.

### Network Lab
Dashboard (Adapter, Gateway, DNS, Linkspeed, WLAN-Signal über die WLAN-API), öffentliche IP nur nach Zustimmung
(eine HTTPS-Anfrage, keine Standortabfrage), Ping & Stabilität (ICMP über `IcmpSendEcho` ohne Admin und TCP-
Handshake; Verlust, Median, p95, Jitter; lokal vs. extern; ICMP-Filter ≠ Ausfall), Bufferbloat nur nach
Zustimmung mit Zeit- und Datenlimit, Bandbreite (echte Adapterraten; pro Programm nur Verbindungen und
Gesamt-I/O, ausdrücklich so beschriftet), Netzwerk-Gaming-Modus (pausiert Hintergrundprogramme über den
FPS-Booster-Mechanismus, misst vorher/nachher, stellt wieder her – auch beim nächsten Start, unabhängig von der
Lizenz), DNS-Benchmark (nie automatische Änderung; Änderung nur nach Bestätigung mit Sicherung und Restore),
Verbindungsreparatur (kein ungefragter Adapter-Reset). Keine Registry-Ping-Tweaks, keine TCP-Optimierungen.

**VPN:** bewusst kein VPN-Treiber, keine eigene Kryptografie, kein simulierter VPN-Knopf. Vorbereitet ist nur
die Erkennung vorhandener VPN-Adapter im Dashboard. Ein späteres VPN-Modul müsste einen etablierten Client
(z. B. WireGuard für Windows) über dessen dokumentierte Schnittstelle steuern, nie eigene Tunnel bauen.

### Universal Mod Scanner
Bibliothek pro Spiel (Quelle AKENO/extern, Versionen, Loader, Pfade, Warnungen, letzter Scan, Status), Suche,
Filter, Sortierung, Details, Neu scannen, Speicherort öffnen, Erkennungsquelle. Minecraft Java (offizieller
Launcher, MultiMC/Prism, CurseForge, Modrinth, ATLauncher; Fabric, Quilt, Forge, NeoForge): JARs werden nur als
Datenarchive gelesen (Zip-Bomb-Grenzen), nie ausgeführt. FiveM: AKENO-verwaltet vs. extern vs. Client-
Komponenten; Server-Mods werden nie als vollständig erkannt behauptet. Adapter: BepInEx, MelonLoader, UE4SS,
Unreal-Paks, ASI-Loader, ReShade, OptiScaler, Plugin-Ordner. Konfliktdetektor meldet nur, was die Metadaten
belegen. Externe Mods sind read-only; AKENOs transaktionale Wiederherstellung bleibt unverändert.

### Security Center
AKENO ist **kein Virenschutz** und behauptet keinen eigenen Echtzeitschutz.

- **Status:** `WscGetSecurityProviderHealth` (Bereiche Firewall, Virenschutz, …), `IWSCProductList`/
  `IWscProduct` (Produkte mit Zustand und Signaturstatus; Fallback `root/SecurityCenter2` nur mit Namen),
  `Get-MpComputerStatus` (Defender-Details), `Get-NetFirewallProfile`.
- **Fremder Virenschutz** wird respektiert: Status wird angezeigt, Scans/Quarantäne/Verlauf bleiben im Produkt.
  Defender im passiven Modus darf vom Nutzer gestartete Scans ausführen.
- **Scans:** `MpCmdRun.exe -Scan -ScanType 1|2|3` (neueste Plattform unter `ProgramData\…\Platform`). Ein vom
  Nutzer gestarteter Scan ist ein normaler Defender-Scan (Defender handelt nach seinen Einstellungen, Eintrag im
  Schutzverlauf). Abbruch beendet den Client und ruft `-Scan -Cancel` auf.
- **Prüfungen ohne Änderung** (Dateien, Mods, Downloads): `-ScanType 3 -File <Pfad> -DisableRemediation` – laut
  Microsoft nur Bericht, keine Aktion, keine Quarantäne. Damit bleiben externe Mods read-only.
- **Gaming & Mod Security Scanner:** echter Typ aus den ersten Bytes, SHA-256, Hersteller (PE-Versionsressource),
  Signatur, Herkunft (Mark of the Web), Defender-Urteil, Programme/Skripte in Archiven. Warnungen nur mit
  Begründung (Fund, gebrochene Signatur, Programm mit Dokument-Endung, doppelte Endung, Verknüpfung auf
  Befehlsinterpreter, Skripte/Bildschirmschoner im Mod-Archiv). Fehlende Signatur ist keine Warnung.
- **Download-Schutz:** `DownloadManager._scanned` läuft *nach* der SHA-256-Prüfung (und ersetzt sie nie). Ein
  Fund (`ThreatDetected`) stoppt die Installation, die Datei bleibt liegen (Defender entscheidet), keine
  Ausnahme wird angelegt. Ohne nutzbaren Virenschutz läuft die Installation mit den bisherigen Prüfungen weiter.
  Angeschlossen an `AppServices.downloads` (Katalog-Backends, PresentMon) und die Komponentenquelle des
  Mod-Stacks (DLSS-Komponenten, ReShade-Setup). Nicht angeschlossen: der signierte AKENO-Updater (eigene
  Ed25519-Kette).
- **Laufende Programme:** Warnung nur bei konkretem Grund (Windows-Name außerhalb des Windows-Ordners, Name eine
  Bearbeitung von einer Windows-Komponente + unsigniert, doppelte Endung, unsigniert aus Temp/Downloads *und*
  online, Pfad in einem ungelösten Defender-Fund). AKENO beendet, pausiert oder löscht hier nichts.
- **Verlauf & Quarantäne:** `Get-MpThreatDetection` + `Get-MpThreat` (lesend); Wiederherstellen/Entfernen nur in
  Windows-Sicherheit (`windowsdefender://history/`).
- Nie: Defender/Firewall/Manipulationsschutz ändern, Ausnahmen, eigener Treiber, automatische Löschung, Uploads.

## 4. Wiederherstellung bei abgelaufener Lizenz

Der Willkommensbildschirm hat den Link **„Spieldateien wiederherstellen…“** (`akeno/ui/recovery_dialog.py`,
`akeno/recovery.py`). Er nutzt dieselbe transaktionale Wiederherstellung wie „Installierte Mods“
(`stack.uninstall` / `stack.remove_legacy`), macht FPS-Booster-Änderungen rückgängig und setzt pausierte
Programme fort. Die Klasse `Recovery` hat ausschließlich Rückgängig-Methoden (per Test festgeschrieben); sie
schaltet keine Funktion frei. Vom Netzwerk-Gaming-Modus pausierte Programme werden zusätzlich bei jedem Start
vor der Lizenzprüfung fortgesetzt (`resume_paused_processes`).

## 5. Echte Daten vs. Testdaten

| Quelle | Unit-/UI-Tests | Windows-CI (`tools/system_check.py`) |
|---|---|---|
| Doctor-Sensoren, Ereignisse, Datenträger | `FakeProbe` | `LocalProbe` (echte Zähler, Event Log, Storage) |
| Cleaner | Fixture-Ordner | Scan der echten Runner-Ordner (nur Scan) |
| Network | Fake-Prober | echte Adapter, kurzer Stabilitätstest, DNS-Benchmark, Bandbreite (kein Lasttest) |
| Mods | Fixture-Instanzen | Bibliotheksscan des Runners |
| Security | `FakeSecurity` | WSC, Defender-Status/Verlauf, Prozessprüfung, Dateiprüfung, EICAR-Testdatei |

Screenshots aus Stand-in-Daten sind als Testdaten gekennzeichnet und nie Hardware-Belege.

## 6. Bekannte Grenzen

- Pro-Programm-Netzwerkverkehr gibt es unter Windows ohne Admin-Tracing (ETW) nicht; angezeigt werden
  Verbindungen und Gesamt-I/O, so beschriftet.
- WLAN-Details können unter Windows 11 24H2 eine Standortfreigabe erfordern; dann steht genau das da.
- Ohne Defender (fremder Virenschutz aktiv) gibt es kein Urteil für Datei- und Download-Prüfungen; das wird als
  „nicht geprüft“ angezeigt, nie als „sauber“.
- 7z/RAR-Archive werden nicht aufgelistet (Defender prüft sie trotzdem).
- Serverseitige FiveM-Ressourcen sind clientseitig nicht vollständig erkennbar.
- Gesundheitsindikator ist eine Orientierung, kein Messwert.
