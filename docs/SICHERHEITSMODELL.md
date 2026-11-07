# Sicherheitsmodell von AI Employee

Stand: 07.11.2026 (Version 1.439.0). Dieses Dokument ist die eine Übersicht, an der sich jede
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
| Anmeldung und Nutzertrennung | fremde Konten, fremde Agenten sehen | `dependencies.get_current_user`, `require_agent_access`, Zwei-Faktor | `api_secret`, `cors`, `kiosk` |
| Identität der Agenten | ausgelaufenes Agenten-Token von außen benutzen, fremde Redis-Postfächer | `dependencies.agent_zugang_pruefen` (einzige Prüfung), Redis-ACL je Agent | `agenten_token`, `redis_acl`, `redis_passwort` |
| Fremdtext | Anweisungen aus Mails, Webseiten, Prüfberichten ausführen | `core/fremdtext.rahmen`, Sentinel | `sentinel` |
| Handlungen | gefährliche Befehle, Löschen, Senden ohne Freigabe | Autonomie-Matrix, Freigaben, Sentinel-Sperre (`CommandPolicy`), verwalteter PreToolUse-Hook (`agent/richtlinie`) | `sentinel` |
| Zugänge | Schlüssel im Agenten, Schlüssel im Klartext in der Datenbank | Schlüsselverwaltung (Fernet, `ENCRYPTION_KEY`), Egress-Platzhalter | `encryption_key`, Schlüssel, Zugänge (Claude/Codex) |
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
3. **Uvicorn vertraut `X-Forwarded-For` von jedem Absender** (`--forwarded-allow-ips "*"`).
   Ein Agent kann die Absenderadresse fälschen, die Protokoll und Ratenbegrenzung sehen.
   → offener Punkt 2.

## Offene Punkte, nach Risiko

| # | Punkt | Schicht | Lösung | Stand |
|---|---|---|---|---|
| 1 | Werkzeuge erben Zugänge | Zugänge | Eine Funktion `werkzeug_umgebung()` für alle vier CLI-Starts (`chat_handler`, `agent_runner`, `message_consumer`, `codex_runner`): `AGENT_TOKEN`, `REDIS_URL`, `BROWSER_PROFIL_KEY`, `CUSTOM_MCP_AUTH`, `CODEX_AUTH_JSON` raus. Die MCP-Server bekommen ihre Werte schon heute ausdrücklich in ihrer Konfiguration. Grenze: der gemeinsame MCP-Prozess läuft unter derselben Kennung. | nächster Schritt |
| 2 | Gefälschte Absenderadresse | Anmeldung | Uvicorn nur Caddy vertrauen lassen statt `*`. | geplant |
| 3 | Kontingent-Bremse für Abo-Konten | Betrieb | Wie die Zeitplan-Kostenbremse, aber für Abo-Kontingente (Claude, ChatGPT). | geplant |
| 4 | Release mit Probelauf | Betrieb (Lieferkette) | Release-Skript mit Trockenlauf, CI-Status von `main` als Pflicht. | geplant |
| 5 | Sentinel-Zugang ist wie das Agenten-Token aus `API_SECRET_KEY` abgeleitet und gilt von überall | Identität | Dieselbe Eingangsprüfung, falls der Sentinel je über HTTP ruft. Heute läuft er im Orchestrator-Prozess. | prüfen |
| 6 | Codex-Zugang auf der Testanlage nur für einen Agenten hinter dem Proxy | Zugänge | Betreiberentscheidung, der Sicherheitsstatus zeigt es. | Information |

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
- Text aus fremden Quellen geht nur gerahmt (`fremdtext.rahmen`) an ein Modell.
- Eine neue Schutzschicht ist erst fertig mit Statuspunkt, Test in beide Richtungen und
  Eintrag in diesem Dokument.
