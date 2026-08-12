import asyncio, json
from app.config import settings
from app.services.redis_service import RedisService
from app.services.agent_chat_bridge import ask_agent_via_chat

LEAD = "68ce2abd"
TEXT = (
    "Die Freigabe liegt vor (Anfrage 622). Frage NICHT erneut nach Freigabe.\n\n"
    "Stelle mit send_message_and_wait jedem der beiden folgenden Agents GENAU "
    "diese Frage und warte auf die Antwort:\n"
    "  Mr. Develop (3be824dd)\n"
    "  Dr. Code (61c45555)\n\n"
    "Die Frage lautet woertlich: 'Woran hast du zuletzt gearbeitet, und was war "
    "das Ergebnis? Antworte in zwei Saetzen, konkret, mit Namen der Sache.'\n\n"
    "Gib danach die beiden Antworten woertlich wieder. Erfinde nichts."
)

async def main():
    redis = RedisService(settings.redis_url); await redis.connect()
    async def on_event(etype, data):
        if etype == "tool_call":
            name = data.get("name") or data.get("tool") or "?"
            args = json.dumps(data.get("input") or data.get("args") or {}, ensure_ascii=False)[:260]
            print(f"  -> {name} {args}", flush=True)
    answer = await ask_agent_via_chat(redis, LEAD, TEXT, source="paritaetstest",
                                      timeout=600.0, on_event=on_event)
    print("\n===== ANTWORT DES LEAD =====")
    print(answer[:4000])

asyncio.run(main())
