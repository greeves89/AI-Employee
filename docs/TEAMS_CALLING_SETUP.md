# Agent mit Stimme im Teams-Termin — Einrichtung in Azure

Damit ein Agent einem Teams-Termin beitreten und dort **sprechen** kann, braucht es
eine Bot-Identität in Azure. Diese Anleitung führt Klick für Klick hindurch.

**Dauer:** etwa 15 Minuten. **Nötig:** ein Konto mit der Rolle *Globaler Administrator*
oder *Anwendungsadministrator* — die Zustimmung in Schritt 5 kann sonst niemand geben.

---

## Was danach geht, und was nicht

| | |
|---|---|
| Der Agent tritt einem Termin bei | ja |
| Er sagt etwas hinein | ja |
| Er hört eine Antwort und reagiert darauf | ja, abwechselnd — wie am Telefon |
| Er hört durchgehend mit und redet dazwischen | **nein** |

Der letzte Punkt braucht den rohen Audiostrom (*application-hosted media*). Microsofts
Echtzeit-Medien-Bibliothek gibt es nur für .NET, dazu kämen offene Medienports und die
weitreichende Berechtigung `Calls.AccessMedia.All`. Dieser Weg hier kommt ohne all das
aus — und deckt ab, was in einer Besprechung tatsächlich gebraucht wird: zuhören,
gefragt werden, antworten.

---

## Vorab: läuft die Anlage unter HTTPS?

**Microsoft ruft ausschließlich HTTPS zurück.** Steht die Anlage nur unter `http`,
kommt keine einzige Benachrichtigung an — und zwar ohne Fehlermeldung, der Agent bleibt
einfach stumm.

Die Einrichtungs-Karte in den Einstellungen zeigt oben, ob das erfüllt ist. Steht dort
eine Warnung, zuerst das lösen (Cloudflare-Tunnel, Reverse-Proxy mit Zertifikat).

---

## Schritt 1 — App-Registrierung anlegen

1. [portal.azure.com](https://portal.azure.com) öffnen
2. Oben suchen: **App-Registrierungen** → **Neue Registrierung**
3. Ausfüllen:
   - **Name:** `AI Employee Meeting Bot`
   - **Unterstützte Kontotypen:** *Nur Konten in diesem Organisationsverzeichnis*
   - **Umleitungs-URI:** leer lassen — wird hier nicht gebraucht
4. **Registrieren**

Auf der Übersichtsseite stehen jetzt zwei Werte, die später gebraucht werden:

- **Anwendungs-ID (Client)** → in der Karte als *App-ID* eintragen
- **Verzeichnis-ID (Mandant)** → in der Karte als *Mandanten-ID* eintragen

---

## Schritt 2 — Geheimnis erzeugen

1. Links **Zertifikate & Geheimnisse**
2. **Neuer Geheimer Clientschlüssel**
3. Beschreibung: `AI Employee`, Gültigkeit nach Hausregel (24 Monate ist üblich)
4. **Hinzufügen**

> **Der Wert ist nur EINMAL sichtbar.** Sofort kopieren — nicht die *Geheimnis-ID*,
> sondern die Spalte **Wert**. Beim nächsten Seitenaufruf ist er verdeckt und muss neu
> erzeugt werden.

Den Wert in der Karte als *Client-Geheimnis* eintragen.

---

## Schritt 3 — Berechtigungen setzen

1. Links **API-Berechtigungen** → **Berechtigung hinzufügen**
2. **Microsoft Graph** → **Anwendungsberechtigungen** (nicht *Delegiert* — ein Bot
   tritt ohne angemeldeten Nutzer bei, delegierte Rechte greifen dort nicht)
3. Diese vier suchen und ankreuzen:

   | Berechtigung | Wofür |
   |---|---|
   | `Calls.JoinGroupCall.All` | Terminen der eigenen Organisation beitreten |
   | `Calls.JoinGroupCallAsGuest.All` | Terminen anderer Organisationen als Gast beitreten |
   | `Calls.InitiateGroupCall.All` | Einen Anruf von sich aus starten |
   | `OnlineMeetings.Read.All` | Den Beitrittslink einer Einladung auflösen |

4. **Berechtigungen hinzufügen**

> `Calls.AccessMedia.All` wird **nicht** gebraucht und sollte auch nicht vergeben
> werden. Sie erlaubt den Zugriff auf den rohen Audiostrom aller Teilnehmer — dieser
> Weg nutzt sie nicht, und ein Recht, das man nicht braucht, vergibt man nicht.

---

## Schritt 4 — Bot-Ressource anlegen und Anrufe einschalten

1. Im Portal suchen: **Azure Bot** → **Erstellen**
2. Ausfüllen:
   - **Bot-Handle:** frei wählbar, z. B. `ai-employee-bot`
   - **Abonnement / Ressourcengruppe:** nach Hausregel
   - **Preis:** *F0 (Free)* reicht
   - **Typ der App:** *Einzelmandant*
   - **App-ID erstellen:** **Vorhandene App-Registrierung verwenden** und die
     Anwendungs-ID aus Schritt 1 eintragen
3. **Überprüfen + erstellen**
4. Nach dem Anlegen: links **Kanäle** → **Microsoft Teams** hinzufügen
5. Im Teams-Kanal den Reiter **Anrufe** öffnen:
   - **Anruffunktionen aktivieren** einschalten
   - **Webhook (für Anrufe)**: hier die Adresse eintragen, die in der
     Einrichtungs-Karte oben steht — sie sieht so aus:

     ```
     https://<deine-adresse>/api/v1/teams/calling/callback
     ```
6. **Anwenden**

---

## Schritt 5 — Zustimmung erteilen

Zurück zur **App-Registrierung** → **API-Berechtigungen** →
**Administratorzustimmung für \<Organisation\> erteilen** → bestätigen.

In der Spalte *Status* muss bei allen vier Einträgen ein grüner Haken stehen. **Ohne
diesen Klick passiert nichts** — die App bekommt zwar ein Token, darf damit aber
keinem Termin beitreten. Das ist der mit Abstand häufigste Grund, warum es beim
ersten Versuch nicht klappt.

---

## Schritt 6 — Richtlinie für den Beitritt (nur bei geschlossenen Terminen)

Sind Termine so eingestellt, dass nur eingeladene Personen direkt hereinkommen, wartet
der Bot sonst in der Lobby. Ein Teams-Administrator setzt dafür einmalig:

```powershell
# Teams-PowerShell-Modul vorausgesetzt
Connect-MicrosoftTeams
New-CsApplicationAccessPolicy -Identity "AI-Employee-Bot" `
  -AppIds "<Anwendungs-ID aus Schritt 1>" `
  -Description "AI Employee darf Terminen beitreten"
Grant-CsApplicationAccessPolicy -PolicyName "AI-Employee-Bot" -Global
```

Die Richtlinie braucht bis zu 30 Minuten, bis sie greift.

---

## Schritt 7 — Prüfen

In der Einrichtungs-Karte auf **Einrichtung prüfen** klicken. Die Antwort ist eine von:

| Meldung | Bedeutung |
|---|---|
| Alles bereit | Token wird ausgestellt, Zustimmung liegt vor |
| Kein Token — App-ID, Geheimnis oder Mandanten-ID stimmen nicht | Schritt 1 und 2 prüfen; meist wurde die *Geheimnis-ID* statt des *Werts* kopiert |
| Token da, aber die Zustimmung fehlt | Schritt 5 wurde übersprungen |
| Graph nicht erreichbar | Netzwerk/Proxy — geht ausgehendes HTTPS zu `graph.microsoft.com`? |

Danach in der Karte **Teams-Anrufe aktivieren** einschalten.

---

## Wenn es nicht klappt

**Der Bot tritt bei, sagt aber nichts.** Fast immer HTTPS: Microsoft konnte die
Rückruf-Adresse nicht erreichen. Prüfen, ob die Adresse aus Schritt 4.5 von außen
aufrufbar ist — ein Aufruf im Browser muss antworten, nicht in einen Zeitablauf laufen.

**Der Bot wartet in der Lobby.** Schritt 6 fehlt oder die Richtlinie greift noch nicht.

**„Forbidden" im Protokoll.** Zustimmung aus Schritt 5 fehlt, oder es wurden
*delegierte* statt *Anwendungs*berechtigungen gewählt.

**Nach Ablauf des Geheimnisses.** Client-Geheimnisse laufen ab. Danach kommt kein
Token mehr, und der Agent bleibt Terminen fern. Neues Geheimnis in Schritt 2 erzeugen
und in der Karte eintragen — sonst ändert sich nichts.

---

## Was in der Anlage gespeichert wird

- **App-ID** und **Mandanten-ID** im Klartext (sind keine Geheimnisse)
- **Client-Geheimnis** verschlüsselt (`SECRET_KEYS`), wird nie im Klartext zurückgegeben
- Die Rückruf-Adresse wird aus der öffentlichen Adresse der Anlage abgeleitet, nicht
  gespeichert — ändert sich die Adresse, muss sie in Azure nachgezogen werden
