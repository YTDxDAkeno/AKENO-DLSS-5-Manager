# UDP 3478: Prüf- und Freigabeplan — nicht angewendet

Der aktuelle Host erlaubt eingehend TCP 22/80/443 über UFW; UDP 3478 ist unbelegt. Der Docker-Forward-Pfad durchläuft DOCKER-USER vor den UFW-Forward-Ketten. DOCKER-USER ist derzeit leer. Deshalb ist `ufw allow 3478/udp` allein weder notwendig noch eine verlässliche Begrenzung eines veröffentlichten Docker-Ports. Kein solcher Befehl wurde ausgeführt.

Die normale Compose-Datei bindet UDP ausschließlich an 127.0.0.1. Für Internettests ist die separate `compose.public-udp.yml` vorgesehen, aber nicht freigegeben. Der Port innerhalb und außerhalb muss 3478 bleiben: die API gibt `coordinator.address[1]` zurück, nicht einen unabhängig konfigurierbaren externen Port. TCP 3478 ist unnötig. Dies ist das eigene WireGuard-Protokoll, kein allgemeiner STUN-/TURN-Server.

Vor einer Freigabe müssen die tatsächlichen öffentlichen IPv4-Adressen beider Tester bekannt sein. Keine anonym offene Quellfreigabe. Bei wechselnder IP erst neue Freigabe prüfen; nicht auf 0.0.0.0/0 erweitern. Kein AAAA-Eintrag und keine IPv6-UDP-Bindung für diesen ersten Test. DNS des UDP-Hosts muss direkt auf die VPS-IPv4 zeigen, ohne HTTP/CDN-Proxy.

## Vorgesehene Regeln zur späteren Einzelprüfung

Diese Befehle sind **nur die Prüfvorschrift**, kein ausgeführter Schritt und kein automatisch gestartetes Skript. Platzhalter sind absichtlich nicht einsatzfähig. Die zwei benannten Chains dürfen zuvor nicht existieren; andernfalls abbrechen. Vor Anwendung aktuelle Regeln extern sichern und Eigentümerfreigabe einholen.

```bash
# PC_A_IPV4 und PC_B_IPV4 sind bestätigte einzelne öffentliche IPv4-Adressen.
# Die Ketten werden vollständig angelegt, bevor ein Sprung dorthin installiert wird.
sudo iptables -N AKENO-STAGE-UDP
sudo iptables -N AKENO-STAGE-BUDGET
sudo iptables -A AKENO-STAGE-BUDGET -m limit --limit 1000/second --limit-burst 1500 -j ACCEPT
sudo iptables -A AKENO-STAGE-BUDGET -j DROP
sudo iptables -A AKENO-STAGE-UDP -s PC_A_IPV4/32 -j AKENO-STAGE-BUDGET
sudo iptables -A AKENO-STAGE-UDP -s PC_B_IPV4/32 -j AKENO-STAGE-BUDGET
sudo iptables -A AKENO-STAGE-UDP -j DROP
sudo iptables -I DOCKER-USER 1 -p udp -m conntrack --ctdir ORIGINAL --ctorigdst 85.190.112.10 --ctorigdstport 3478 -j AKENO-STAGE-UDP
```

Der Budgetzähler ist für beide Tester gemeinsam; maximal ein Paket wird pro Eingang weitergegeben. Das Backend prüft WireGuard-Formate bis 1.532 Byte und zusätzlich 400 Pakete/Sekunde pro Quell-IP+Port. 1.000 angenommene Pakete/Sekunde entsprechen grob bis zu 12,5 Mbit/s je Richtung/Übertragungsstrecke inklusive IPv4/UDP, ohne Link-Overhead. Das ist kein Schutz vor bereits an der VPS-Netzkarte ankommender DDoS-Bandbreite und kein garantierter Tarif-/Egress-Kostenrahmen. Provider-Limits sind nicht geprüft. Bei relevantem Risiko getrennten Testhost verwenden.

Erst danach darf der öffentliche UDP-Override gestartet werden. Anonyme Pakete, falsche Mitgliedschaft und abgelaufene Leases müssen verworfen werden. Gegenprobe von einer dritten nicht erlaubten Quelle, echte Gegenprobe von beiden PCs und Prüfung `iptables -nvL AKENO-STAGE-UDP`/`AKENO-STAGE-BUDGET` sind erforderlich. Ein TCP-Portscan allein bestätigt keinen UDP-Pfad.

## Rücknahme nach späterer Freigabe

Zuerst nur das Staging-Projekt stoppen. Anschließend den exakten DOCKER-USER-Sprung entfernen, dann die beiden ausschließlich dafür angelegten Chains leeren und löschen. Keine ganze DOCKER-USER-Kette leeren und keine globale iptables-/UFW-Sicherung blind zurückspielen: andere Arbeiten könnten inzwischen legitime Regeln hinzugefügt haben. Nach Docker-Neustart darf der öffentliche Test nicht automatisch anlaufen; Compose verwendet `restart: "no"`. Persistenz der ACL vor einem bewussten Neustart erneut prüfen.

Es wurden weder Host- noch Provider-Firewallregeln geändert. Das Provider-Panel ist hier nicht verbunden; dessen Filter bleiben ungeprüft.
