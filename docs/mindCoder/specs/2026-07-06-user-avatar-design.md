# Design: User-Avatar aus SSO (Sidebar + Chat-Bubbles)

**Datum:** 2026-07-06
**Scope:** Backend (`orchestrator/app/api/auth.py`) + Frontend
**Status:** Vom User genehmigt

## Anforderung

Der per SSO angemeldete User soll seinen Avatar unten links in der Sidebar
und in den Chat-Bubbles sehen. Gibt es kein Avatar-Bild, reichen die
Initialen.

## Lösung

### Backend: `GET /api/v1/auth/me/photo`

Der Microsoft-SSO-Login speichert bereits pro User ein Graph-Token
(`oauth_integrations`, Capture in `sso_service.py`). Der neue Endpoint:

- authentifiziert den User (`get_current_user`),
- holt via `OAuthService(db, None).get_valid_token("microsoft", user.id)`
  ein gültiges Token (Auto-Refresh),
- proxied `GET https://graph.microsoft.com/v1.0/me/photo/$value` und liefert
  die Bytes mit Original-Content-Type und `Cache-Control: private,
  max-age=3600` aus,
- antwortet `404` bei: kein Microsoft-SSO-User, kein Token, kein Foto
  hinterlegt, Graph-Fehler. `404` ist das Fallback-Signal fürs Frontend.

Kein DB-Schema-Change, keine neuen Scopes (Login fordert bereits volle
Graph-Scopes an).

### Frontend: `UserAvatar`-Komponente

`frontend/src/components/ui/user-avatar.tsx`:

- lädt das Foto einmal pro Page-Load (`fetch` mit Credentials → Blob →
  Object-URL) und cached es modulweit — der Chat rendert einen Avatar pro
  Bubble, es entsteht trotzdem nur EIN Request,
- Fallback bei 404/Fehler: Initialen (erste Buchstaben der Namensteile,
  max. 2, uppercase) im bisherigen Look (`bg-primary/10 text-primary`),
- Größe/Form über `className` steuerbar.

Einsatzstellen:

- `layout/user-menu.tsx` (Sidebar unten links): ersetzt die bisherige
  Initialen-Box, Online-Dot bleibt.
- `agents/chat.tsx` `UserMessage`: ersetzt das blaue lucide-`User`-Icon
  (6x6, `rounded-md`); Name kommt aus `useAuthStore`.

## Nicht im Scope

- Kein Avatar für Google-SSO (dort wäre der `picture`-Claim der Weg —
  separates Paket bei Bedarf).
- Kein Avatar-Upload für lokale (Nicht-SSO-)Accounts — sie sehen Initialen.
- Keine Anzeige fremder Avatare (Admin-Userliste etc.).

## Testen

- Build-Verification (next build + Python-Syntax).
- Initialen-Fallback: lokale Accounts sehen sofort Initialen (kein Request-
  Fehlerbild). Foto-Pfad braucht einen echten Microsoft-SSO-Login (SKBS) —
  Verifikation dort nach Deploy.
