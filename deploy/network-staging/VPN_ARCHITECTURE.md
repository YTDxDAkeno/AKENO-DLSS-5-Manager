# AKENO VPN: Architektur zusätzlicher eigener Exit-Server

LAN Play und Internet-VPN sind getrennte Dienste. Der vorbereitete VPS-Koordinator erstellt kein TUN-Interface, keine Betriebssystem-WireGuard-Verbindung, kein IP-Forwarding und kein NAT. Er beantwortet den Authentisierungs-Handshake und vermittelt begrenzten, Ende-zu-Ende-verschlüsselten Raumverkehr. Es gibt hier keinen Internet-Exit.

Für spätere Standorte USA, Türkei und weitere Länder ist jeweils ein **separater Exit-Host** mit eigener statischer WireGuard-Identität vorzusehen. Noch keine Anbieter-/Standortverfügbarkeit behaupten, keinen Server kaufen und keine öffentliche Probeinstanz starten. Providerbedingungen, dortiges Recht, Abuse-Prozess und tatsächliche Kosten sind vor Beschaffung gesondert zu klären.

Die vorhandene Windows-Implementierung unterstützt importierte eigene WireGuard-Profile mit einem Peer, geprüften Schlüsseln, IPs, Endpoint und MTU. Ein dynamischer Länderkatalog mit kommerzieller Abrechnung, automatischer Peer-Zuteilung und sicherem Provisionierungs-API ist dadurch noch nicht implementiert.

Späterer Sollpfad:

1. Ein ausdrücklich aktivierter VPN-Control-Plane-Dienst bestätigt das VPN-spezifische Nutzungsrecht und registriert nur den öffentlichen Geräteschlüssel. Lizenzdatenbank-Zugang erhält ein Exit-Host nicht.
2. Pro Standort wird ein fest gepinnter öffentlicher Exit-Schlüssel angeboten; private Exit-Schlüssel bleiben auf ihrem jeweiligen Exit-Host. Der private Geräteschlüssel bleibt lokal unter Windows-DPAPI.
3. Nur authentifizierte, zeitlich begrenzte Gerätepeers werden auf dem gewählten Exit eingetragen. Keine anonyme freie Proxy-/Relay-Nutzung. Limits für Geräte, Laufzeit und Bandbreite, Widerruf und Abuse-Abschaltung gehören auf den Exit.
4. Nur diese neuen Exit-Hosts erhalten gegebenenfalls WireGuard, Forwarding, IPv4/IPv6-Routing, NAT und geeignete Egress-/DNS-Regeln. Ein üblicher eigener WireGuard-Port wäre UDP 51820; dies ist ein künftiges Beispiel, kein auf dem AKENO-VPS reservierter oder geöffneter Port.
5. LAN-Raumrouten bleiben exakte `/32`-Peers innerhalb `10.203.0.0/16`; Internet-VPN kann nach ausdrücklicher Nutzerentscheidung Default-Routen verwenden. Kollidierende LAN-/VPN-/lokale Netze müssen vor Verbindung abgelehnt werden. Kein Routing zwischen LAN-Räumen und Exit-Netzen.
6. Kill-Switch, IPv4/IPv6 und DNS-Leaks, Exit-IP, Reconnect, Standortwechsel sowie sichere Wiederherstellung nach Fehlern werden pro tatsächlich installiertem Exit und auf echten Windows-PCs geprüft. CI-Nachweise ersetzen diese Prüfung nicht.

Für den ersten LAN-Test bleibt VPN Gate deaktiviert. Es wird kein fremder VPN-Anbieter angebunden und keine bestehende produktive Lizenz- oder Release-Konfiguration erweitert. Die spätere VPN-Identität/Produktberechtigung ist getrennt von LAN-Mitgliedschaft zu entwerfen. Pro Standort müssen klar bezeichnete Betriebs-, Datenschutz- und Aufbewahrungsregeln gelten; keine unbelegte No-Logs-Zusage.
