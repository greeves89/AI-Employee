# AI-Employee Desktop

Ein **einfacher Chat-Client** für dein KI-Team – im Stil von ChatGPT / Claude Desktop.

- **Einstieg:** Login → **Agenten-Auswahl**
- **Danach:** Chat-Fenster mit **Chats links** in der Seitenleiste
- **Voice + Text** in einem Fenster
- Später: Programmier-Modus (Codex-Style)

## Aufbau

```
desktop-app/
├── index.html      # 3 Screens: Login · Agenten · Chat  (+ Voice-Overlay)
├── styles.css      # Dark-Theme, Blau #3b82f6
├── app.js          # Client-Logik → orchestrator REST + WebSocket
├── package.json
└── src-tauri/       # Native Fenster-Shell (Tauri v2)
```

Die App ist **buildfree**: `index.html` + `styles.css` + `app.js` laufen direkt im Browser.
Tauri verpackt exakt diese Dateien später als native Desktop-App (~10 MB).

## Lokal ausprobieren (ohne Build)

```bash
cd desktop-app
python3 -m http.server 4321     # oder: npm run dev
# → http://localhost:4321 öffnen
```

Beim Login eingeben:
- **Server:** URL deiner AI-Employee-Instanz (z. B. `https://server.ai-employee.de`)
- **E-Mail / Passwort:** deine Zugangsdaten

> Hinweis: Der Server muss CORS für die Origin erlauben, wenn die App im Browser läuft.
> In der nativen Tauri-App entfällt das (kein CORS, da eigene App-Origin).

## Genutzte Server-Endpunkte

| Zweck              | Endpoint |
|--------------------|----------|
| Login              | `POST /api/v1/auth/login` → `access_token` |
| Agenten-Liste      | `GET  /api/v1/agents/` |
| Chat (Streaming)   | `WS   /api/v1/ws/agents/{id}/chat?token=` |
| Chat-Liste         | `GET  /api/v1/agents/{id}/chat/sessions` |
| Chat-Verlauf       | `GET  /api/v1/agents/{id}/chat/history?session_id=` |
| Voice              | `WS   /api/v1/ws/agents/{id}/voice?token=` |

### Chat-WS-Protokoll
Client → Server: `{"text": "...", "session_id": "...", "source": "webapp"}`
Server → Client: `{"type": "ready|session|text|tool|done|security_block|cancelled|error", "data": {...}}`
Streaming-Text kommt in `type:"text"` als `data.text`, Ende bei `type:"done"`.
Stoppen: `{"action": "stop"}`.

## Als native App verpacken (Tauri)

Voraussetzung: Rust/Cargo installiert (`cargo` war in der Build-Umgebung nicht vorhanden).

```bash
cd desktop-app
npm install
npm run tauri:build      # erzeugt Installer für macOS / Windows / Linux
```

Icons unter `src-tauri/icons/` ablegen (`tauri icon <logo.png>` generiert alle Größen).
