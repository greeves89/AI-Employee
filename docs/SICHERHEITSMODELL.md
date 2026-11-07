# Sicherheitsmodell von AI Employee

Stand: 07.11.2026 (Version 1.441.0). Dieses Dokument ist die eine Übersicht, an der sich jede
Sicherheitsänderung ausrichtet: welche Schichten es gibt, wo jede durchgesetzt wird, wo man sie
sieht und was noch offen ist. Betriebsanleitungen stehen im [Security Guide](SECURITY.md) und im
[Benutzerhandbuch](benutzerhandbuch/README.md).

## Grundsatz

Ein Agent ist **nicht vertrauenswürdig**. Er liest fremde Texte (Mails, Webseiten, Dateien,
Tickets) und kann durch sie gesteuert werden. Jede Schicht geht davon aus, dass der Agent im
schlimmsten Fall tut, was ein Angreifer will. Deshalb gilt für jede Schicht:

1. **Eine Durchsetzungsstelle** im Code. Nicht sieben Stellen, die dasselbe je für sich prüfen.
2. **Ein Punkt im Sicherheitsstatus** (Admin → Betrieb), der zeigt, ob die Schicht wirkt.
3. **Tests in beide Richtungen**: Der Schutz greift, und das Erlaubte geht weiter.

## Die Schichten

| Schicht | Verhindert | Durchsetzung | Sichtbar im Sicherheitsstatus |
|---|---|---|---|
| Anmeldung und Nutzertrennung | fremde Konten, fremde Agenten sehen, Anmelde-Bremse umgehen | `dependencies.get_current_user`, `require_agent_access`, Zwei-Faktor, Absenderadresse nur vom Eingang (`core/vertraute_proxys`) | `api_secret`, `cors`, `kiosk`, `absender` |
| Identität der Agenten | ausgelaufenes Agenten- oder Sentinel-Token von außen benutzen, fremde Redis-Postfächer | `dependencies.agent_zugang_pruefen` (einzige Prüfung, auch in `require_sentinel`), Redis-ACL je Agent | `agenten_token`, `redis_acl`, `redis_passwort` |
| Fremdtext | Anweisungen aus Mails, Webseiten, Prüfberichten ausführen | `core/fremdtext.rahmen`, Sentinel | `sentinel` |
| Handlungen | gefährliche Befehle, Löschen, Senden ohne Freigabe | Autonomie-Matrix, Freigaben, Sentinel-Sperre (`CommandPolicy`), verwalteter PreToolUse-Hook (`agent/richtlinie`) | `sentinel` |
| Zugänge | Schlüssel im Agenten, Schlüssel im Klartext in der Datenbank, Server-Zugänge in Werkzeug-Ausgaben | Schlüsselverwaltung (Fernet, `ENCRYPTION_KEY`), Egress-Platzhalter, Server-Werte nicht in der Umgebung (`agent/app/serverwerte.py`) | `encryption_key`, Schlüssel, Zugänge (Claude/Codex) |
| Ausgang | Schlüssel an falsche Ziele, Zugriff aufs Heimnetz | Egress-Proxy (Schlüssel nur an ihre freigegebenen Ziele), Netzwächter (`scripts/netzwaechter.py`), DLP-Filter | `dlp`, Zugänge |
| Container und Host | Ausbruch, Docker-Steuerung, Heimnetz, Host | Docker-Wächter (`docker_waechter/regeln.py`), Seccomp, Netzwächter, signierte Abbilder | Docker-Punkte, Abbild-Signaturen |
| Betrieb | veraltete Agenten, Datenverlust, fehlende Spur | Agenten-Aktualisierung im Leerlauf, Sicherung, Prüfprotokoll, Aufbewahrung | Agenten-Punkte |
| Vertrauen in Ergebnisse | „erledigt“ ohne Prüfung | Beleg-Regel (`core/beleg.py`), blinder Prüfer (`core/pruefung.py`) | Board-Karte |

## Befunde vom 07.11.2026 (live auf der Testanlage geprüft)

1. **Agenten-Token galt auch von außen.** Das Token des Demo-Agenten wurde über die
   öffentliche Adresse (Tunnel → Caddy → Orchestrator) mit 200 angenommen. Das Token ist aus
   `API_SECRET_KEY` abgeleitet und ändert sich nie: Ein Leck über eine Build-Ausgabe, ein
   Paket-Skript oder Prompt-Injection wäre ein dauerhafter Zugang von außen gewesen. Geprüft
   wurde es an sieben Stellen, jede mit eigenem Vergleich.
   **Behoben in 1.439.0:** eine Prüfung (`agent_zugang_pruefen`). Ein Token gilt nur, wenn die
   Anfrage direkt aus einem Container kommt: ohne Proxy-Kopfzeilen (Caddy setzt
   `X-Forwarded-For` und `Via` bei jeder Anfrage, auch gegen den `Connection`-Trick, gegen das
   Caddy-Abbild der Anlage nachgestellt) und nicht über den veröffentlichten Port des
   Orchestrators auf dem Host (kommt vom Gateway des Docker-Netzes an). Ein gültiges Token von außen landet im Prüfprotokoll
   (`agent_token_extern`) und färbt den Sicherheitsstatus rot. Ein Test verhindert eine
   achte Vergleichsstelle.
2. **Der Ausgang ins Internet ist offen, mit Absicht.** Agenten brauchen das offene Web. Der
   Egress-Proxy ersetzt Platzhalter durch Schlüssel und bricht TLS nur bei Zielen auf, für die
   ein Schlüssel freigegeben ist. Alle anderen HTTPS-Ziele reicht er unbesehen durch. Der
   Netzwächter zwingt nur Port 443 durch den Proxy, andere Ports (80, 8080, 8443 …) gehen
   direkt hinaus. Schlüssel sind dadurch nicht gefährdet (im Agenten liegen nur Platzhalter),
   Arbeitsdaten könnten aber an jedes Ziel abfließen, ob durch den Proxy oder an ihm vorbei.
   Alle Ports durch den Proxy zu zwingen, änderte daran wenig. Wirksam wäre nur eine Zielliste
   je Agent → Entscheidung, siehe unten.
3. **Uvicorn vertraute `X-Forwarded-For` von jedem Absender** (`--forwarded-allow-ips "*"`).
   Ein Agent im Docker-Netz, bei der Community-Variante sogar ein Angreifer aus dem Internet,
   konnte eine beliebige Absenderadresse vorgeben (Anmelde-Bremse, Rate-Limit, Protokoll).
   **Behoben in 1.440.0:** uvicorn wertet keine Proxy-Kopfzeilen mehr aus. Die äußerste
   Middleware tut es nur, wenn die Gegenstelle der Eingangs-Proxy ist (`TRUSTED_PROXY_HOSTS`).
   Live gefunden: Eine eigene `docker-compose.override.yml` kann den Startbefehl mit dem alten
   Schalter überschreiben. Seit 1.440.1 warnt der Sicherheitsstatus (`absender`), weil er die
   Startargumente des laufenden Servers liest.
4. **Werkzeuge erbten Server-Zugänge.** Redis-Anmeldung, Browserprofil-Schlüssel, Zugänge zu
   eigenen MCP-Servern, der eigene Codex-Zugang und der Ausweis am Browser-Bildstrom standen in der Umgebung jedes Kindprozesses
   (CLI, Bash-Befehle, Paket-Skripte, gemeinsamer MCP-Prozess).
   **Behoben in 1.440.0:** Der Agenten-Server nimmt sie beim Start an einer Stelle aus der
   Umgebung (`serverwerte.uebernehmen`). Seine eigene `/proc/<pid>/environ` ist für den
   Agenten-Nutzer nicht lesbar (nicht „dumpbar“, `richtlinie.pruefserver_abschirmen`, live
   geprüft; scheitert das, meldet der Agent es laut). Auch die npm-Paket-Skripte beim
   Containerstart (`entrypoint.sh`, als root) bekommen die Werte nicht mehr.
   **Was das nicht abdeckt:** Zugänge, die eine CLI selbst braucht, liegen weiter dort, wo die
   CLI sie liest: Claude Code führt die Kopfzeilen eigener MCP-Server in `~/.claude.json`,
   Codex bekommt sie als `MCP_TOKEN_<name>`, der eigene Codex-Zugang liegt in
   `~/.codex/auth.json`. Hinter dem Egress-Proxy sind das Platzhalter, die internen
   MCP-Dienste (Mail, Exchange, Graph) tragen das Agenten-Token, das nur von innen gilt.
   Das Agenten-Token bleibt für Werkzeuge (`ai-team`, Proxy-Adresse).
5. **Sentinel-Zugang** ist wie das Agenten-Token abgeleitet und ewig gültig. Kein Endpunkt
   nimmt ihn an (geprüft). Seit 1.440.0 gilt für `require_sentinel` trotzdem dieselbe
   Eingangsprüfung, und ein Test meldet den ersten Endpunkt, der ihn nutzt.

## Offene Punkte, nach Risiko

| # | Punkt | Schicht | Lösung | Stand |
|---|---|---|---|---|
| 1 | Kontingent-Bremse für Abo-Konten | Betrieb (Verfügbarkeit) | Meldet ein Abo-Konto (Claude, ChatGPT) „Limit erreicht“, halten Zeitpläne und Hintergrundläufe bis zur Freigabezeit an, statt in Schleife weiterzulaufen. Wie die Zeitplan-Kostenbremse. | nächster Schritt |
| 2 | Codex-Zugang auf der Testanlage nur für einen Agenten hinter dem Proxy | Zugänge | Betreiberentscheidung, der Sicherheitsstatus zeigt es. | Information |

### Erledigt am 07.11.2026

| Punkt | Version |
|---|---|
| Agenten-Token nur aus dem Agentennetz | 1.439.0 |
| Absenderadresse nur vom Eingangs-Proxy | 1.440.0 |
| Werkzeuge erben keine Server-Zugänge | 1.440.0 |
| Sentinel-Zugang nur von innen | 1.440.0 |
| Absenderadresse: Warnung bei Override im laufenden Server | 1.440.1 |
| Release-Läufer mit Probelauf und harten Prüfungen (`scripts/release.py`) | 1.441.0 |

### Braucht eine Entscheidung

- **Zielliste für den Ausgang je Agent** (Befund 2). Etwa: Agenten eines Fachbereichs dürfen nur
  die freigegebenen Dienste erreichen, Recherche-Agenten das offene Web. Umsetzung im
  Netzwächter (alle Ports durch den Proxy) und im Proxy (Ziel prüfen auch ohne Schlüssel).
  Das ändert, was Agenten können, und braucht eine Voreinstellung je Anlage.
- **Anonymisierer vor dem Modell.** Der DLP-Filter prüft nur, was hinausgeht. Personendaten
  in Aufträgen gehen heute ungefiltert an den Modellanbieter.
- **Strengere Sentinel-Bindung.** Ein Einmalwert je Sperre statt der heutigen Bindung an den
  offenen Chat-Zug.
- **Eigene Identität für Agenten** (Postfach, Microsoft-365-Konto) und Ereignisse bei neuen
  Mails. Das berührt Lizenzen und Rechte beim Kunden.

## Regeln für neue Funktionen

- Ein Agenten-Token wird **nur** mit `agent_zugang_pruefen` geprüft
  (`tests/test_agenten_zugang_nur_innen.py` schlägt sonst an).
- Neue Zugänge für Agenten laufen über die Schlüsselverwaltung und den Egress-Proxy, nie als
  Klartext in der Umgebung.
- Ein Zugang, den nur der Agenten-Server braucht, gehört in `serverwerte.NUR_SERVER` und wird
  nur über `serverwerte.get` gelesen (`agent/tests/test_serverwerte.py` schlägt sonst an).
- Proxy-Kopfzeilen wertet nur `core/vertraute_proxys` aus, nie ein Endpunkt selbst.
- Text aus fremden Quellen geht nur gerahmt (`fremdtext.rahmen`) an ein Modell.
- Eine neue Schutzschicht ist erst fertig mit Statuspunkt, Test in beide Richtungen und
  Eintrag in diesem Dokument.
