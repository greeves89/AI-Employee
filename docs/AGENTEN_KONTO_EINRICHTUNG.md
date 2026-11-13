# Eigene Microsoft-365-Konten für Agenten: Checkliste für die IT

Diese Checkliste ist für die IT eines Unternehmens, das AI Employee betreibt. Ein Agent bekommt
damit ein eigenes, lizenziertes Microsoft-365-Konto. Er liest und schreibt dann als er selbst und
nicht über das Postfach eines Mitarbeiters.

Technischer Hintergrund: `docs/plaene/agenten-identitaet-m365.md`, Sicherheitsmodell:
`docs/SICHERHEITSMODELL.md`, Bedienung: Benutzerhandbuch Kap. 37.16 und Kap. 34.

## 1. Was AI Employee dafür nutzt

- **Delegierte Anmeldung (OAuth):** Der Besitzer des Agenten meldet sich einmal mit den
  Zugangsdaten des Agenten-Nutzers an. Danach erneuert AI Employee das Token selbst. Ein Kennwort
  des Agenten-Nutzers wird nirgends gespeichert.
- **Keine Anwendungsberechtigungen (App-only):** Der Agent kann nur, was sein Nutzer kann. Er hat
  keinen Zugriff auf fremde Postfächer im Mandanten.
- **Dieselbe App-Registrierung** wie für die Microsoft-Anbindung der Mitarbeiter. Für Agenten
  kommt keine neue Registrierung dazu.

## 2. App-Registrierung in Entra ID (einmalig)

Ist die Microsoft-Anbindung schon eingerichtet, ist hier nichts zu tun. Sonst:

- [ ] App registrieren, Kontotyp „nur dieses Organisationsverzeichnis“ (ein Mandant).
- [ ] Umleitungs-URIs (Web):
  - `https://<Adresse der Anlage>/api/v1/integrations/microsoft/callback` (Microsoft-Konten,
    auch die der Agenten)
  - `https://<Adresse der Anlage>/api/v1/auth/sso/microsoft/callback` (nur bei Anmeldung per SSO)
- [ ] Geheimer Clientschlüssel anlegen und in AI Employee unter Admin → Integrationen eintragen.
- [ ] **Delegierte** Berechtigungen für Microsoft Graph. Pflicht sind `openid`, `email`, `profile`,
  `offline_access` und `User.Read`. Für die Agenten werden gebraucht:

  | Berechtigung | Wofür |
  |---|---|
  | `Mail.ReadWrite`, `Mail.Send` | Posteingang lesen, Entwürfe, Senden (nur nach den Regeln in Abschnitt 6) |
  | `Chat.ReadWrite`, `Chat.ReadBasic` | Teams-Chats lesen und darin antworten |
  | `ChannelMessage.Read.All`, `ChannelMessage.Send`, `Team.ReadBasic.All` | Teams-Kanäle lesen und darin antworten |
  | `Calendars.ReadWrite` | Kalender des Agenten |
  | `Files.ReadWrite` | OneDrive des Agenten |

  Weitere angebotene Berechtigungen (`Tasks.ReadWrite`, `Contacts.ReadWrite`, `People.Read`,
  `OnlineMeetings.Read`, `OnlineMeetingTranscript.Read.All`) wählt der Admin in AI Employee ab,
  wenn sie nicht gebraucht werden. AI Employee fordert nur an, was dort ausgewählt ist.
- [ ] Zustimmung: Entweder stimmt ein Administrator für die Organisation zu, oder jeder Nutzer
  stimmt bei der ersten Anmeldung selbst zu. Einige Kanal-Berechtigungen
  (`ChannelMessage.Read.All`) verlangen die Zustimmung eines Administrators.

## 3. Je Agent: Nutzer anlegen

- [ ] Nutzer anlegen, z. B. `assistenz@<domäne>`. Er bekommt keine Administratorrolle und gehört
  keinen Gruppen mit weitreichenden Rechten an.
- [ ] Lizenz mit Exchange-Online-Postfach. Für Teams-Chats und -Kanäle zusätzlich Teams.
- [ ] Nur die Teams und Postfächer freigeben, die der Agent wirklich braucht. Der Agent sieht genau
  das, was dieser Nutzer sieht.
- [ ] Optional: Postfach-Regeln, z. B. kein automatisches Weiterleiten nach außen.

## 4. Anmeldung, MFA und Conditional Access

- Die Anmeldung läuft einmal interaktiv, im Browser des Besitzers, der dabei die Zugangsdaten
  des Agenten-Nutzers verwendet. Danach arbeitet AI Employee mit dem Erneuerungs-Token.
- [ ] **MFA:** Der Agenten-Nutzer braucht eine Methode, die der Besitzer bei der Anmeldung
  bedienen kann, z. B. einen eigenen Schlüssel oder eine Authenticator-App des Teams. Die Methode
  wird nur beim Verbinden und beim Neuverbinden gebraucht.
- [ ] **Anmeldehäufigkeit (Conditional Access):** Eine Regel, die regelmäßig eine neue Anmeldung
  erzwingt, beendet den Zugang des Agenten. Für die Agenten-Nutzer eine passende Häufigkeit
  festlegen oder sie bewusst ausnehmen.
- Erneuerungs-Token verfallen nach 90 Tagen ohne Nutzung, nach einem Kennwortwechsel und nach
  dem Widerruf der Sitzungen. Dann warnt AI Employee: Meldung an den Besitzer,
  Sicherheitsstatus „Microsoft-Konten der Agenten“. Der Agent arbeitet dann **nicht** mit dem
  Konto seines Besitzers weiter, er hört auf, bis jemand neu verbindet.

## 5. Benachrichtigung bei neuer Mail (optional)

- Ist AI Employee über HTTPS aus dem Internet erreichbar, meldet Microsoft neue Mails sofort an:
  - `https://<Adresse der Anlage>/api/v1/msgraph/benachrichtigung`
  - `https://<Adresse der Anlage>/api/v1/msgraph/lebenszyklus`
- [ ] Diese beiden Adressen von Microsoft aus erreichbar machen (gültiges Zertifikat, POST
  erlaubt). Jede Meldung trägt ein Geheimnis, das nur AI Employee und Microsoft kennen.
  Meldungen ohne passendes Geheimnis verwirft die Anlage.
- Ohne öffentliche Adresse schaut AI Employee alle fünf Minuten selbst nach. Das funktioniert
  auch hinter einer Firewall, nur mit Verzögerung.
- Voreingestellt wecken nur Absender aus der eigenen Organisation. Das erkennt AI Employee am
  Urteil von Exchange (`X-MS-Exchange-Organization-AuthAs: Internal`), nicht an der
  Absenderadresse.

## 6. Was der Agent darf (Regeln in AI Employee)

Unabhängig vom Konto gelten dieselben Regeln:

- **Nur lesend** für die ganze Anlage (Admin), sonst je Agent: Lesen / Entwurf / Schreiben.
- **Rechte-Matrix** „E-Mail / M365 senden“: Erlaubt / Freigabe / Verboten. Sekretariat-Agenten
  senden nie direkt.
- Im Modus **Entwurf** gehen Mails und Teams-Nachrichten als Freigabe an den Menschen. Gesendet
  wird erst danach, vom Server, genau der freigegebene Text.
- Ein Agent kann nicht mit dem Konto seines Besitzers verbunden werden. Die Anlage lehnt ab, wenn
  sich jemand versehentlich mit dem eigenen Konto anmeldet.
- Verbinden, Trennen und Änderungen an den Kanälen stehen im Prüfprotokoll.

## 7. Abnahme

| Schritt | Erwartet |
|---|---|
| Agent → Konnektoren → „Microsoft-Konto des Agenten“ → Konto verbinden, mit dem Agenten-Nutzer anmelden | Karte zeigt das Konto und „Verbunden“ |
| Dasselbe mit dem eigenen Konto des Besitzers versuchen | Abgelehnt: „Das ist dein eigenes Microsoft-Konto …“ |
| „Bei neuer Mail wecken“ einschalten, interne Mail an den Agenten schicken | Ein Auftrag „Neue Mail: …“ startet (sofort bzw. binnen fünf Minuten) |
| Mail von außen an den Agenten schicken | Kein Auftrag (nur interne Absender), es sei denn, der Haken „Auch Absender außerhalb“ ist gesetzt |
| Admin → Betrieb → Sicherheitsstatus | „Microsoft-Konten der Agenten“ grün, mit Weg „Benachrichtigung“ oder „Abfrage“ |
| Konnektoren → Kanäle → Teams → Chat auswählen, Agent im Chat erwähnen | Agent antwortet (Schreiben) bzw. legt eine Freigabe an (Entwurf) |

## 8. Agent außer Betrieb nehmen

- [ ] In AI Employee: Agent → Konnektoren → „Microsoft-Konto des Agenten“ → **Trennen**. Das
  löscht das Abo bei Microsoft und schaltet das Wecken aus.
- [ ] In Entra ID: Nutzer sperren, Sitzungen widerrufen, Lizenz freigeben.
