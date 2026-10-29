# Anfrage an das VPN-Gate-Projekt (Entwurf, nicht versendet)

**Warum:** AKENO nutzt die öffentliche VPN-Gate-Serverliste für das kostenlose Ein-Klick-VPN. Die Startseite
erlaubt eigene Clients ausdrücklich („Instead you can use CSV List to make your own VPN Gate client app“). Die Server
der Universität tragen aber den Hinweis „Academic Use Only“. Die FAQ sagt außerdem, dass japanische Universitäten
keine Gewinnerzielung betreiben. AKENO ist ein lizenzpflichtiges Produkt, und mit „AKENO VPN“ gäbe es sogar eine
eigene, verkaufbare Lizenz für das VPN-Modul. Ob das erlaubt ist, beantworten die öffentlichen Seiten nicht.

**Empfehlung:** Bis zur schriftlichen Antwort bleiben AKENO-VPN-Lizenzen im Admin-Panel gesperrt (Einstellung
„AKENO-VPN-Lizenzen dürfen ausgegeben werden“ = aus). Für Full-Nutzer bleibt das kostenlose VPN ein unbezahltes
Zusatzangebot; auch das sollte die Anfrage abdecken.

**Kontakt:** Die Kontaktadresse bitte selbst von der offiziellen Seite `https://www.vpngate.net/` übernehmen
(Rubrik „Contact“ / FAQ). Hier ist absichtlich keine Adresse eingetragen: Diese Arbeitsumgebung kann die Seite nicht
abrufen, und eine Adresse wird nicht geraten.

---

**Subject:** Permission request: VPN Gate server list in a Windows gaming application (AKENO Gaming Suite)

Dear VPN Gate team,

I develop the AKENO Gaming Suite, a Windows application for PC gamers. Following your note that developers can use
the CSV server list to build their own VPN Gate client, it includes a one-click client for your network:

* It reads the official list (`https://www.vpngate.net/api/iphone/`) at most once per minute and caches it for ten
  minutes, without the OpenVPN profiles.
* It connects with the official OpenVPN client. It only accepts servers whose certificate matches `opengw.net`, and it
  never changes or relays your servers' configuration beyond the standard client settings.
* It names VPN Gate and the University of Tsukuba as the provider. It never presents your servers as our own.
* Before the first use, it tells users that VPN Gate and server operators log connections (as described in your
  anti-abuse policy) and that no anonymity is promised.
* It never uses the list for blocking.

AKENO is a paid application. Users pay for the suite as a whole; we would like to offer the VPN Gate client to them
without any surcharge. We are also considering a separate license that unlocks only the VPN part of the application
(for example "AKENO VPN"). The VPN Gate servers themselves would stay free and unchanged.

Because the university servers are marked "Academic Use Only", we would like to ask:

1. May a paid application include a VPN Gate client as described above?
2. May we offer a separately licensed "VPN" module of our application that uses VPN Gate servers?
3. Are there conditions you would like us to follow? For example attribution, a link, rate limits, excluding the
   university servers, or a notice text.

We will not publish the VPN Gate integration in a paid offering until we have your answer. Thank you for VPN Gate
and for your time.

Kind regards,
[Name], AKENO Gaming Suite
[E-mail address]
