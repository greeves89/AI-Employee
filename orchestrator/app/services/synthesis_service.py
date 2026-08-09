"""Woechentliche Synthese (#384) — was zieht sich durch die letzten sieben Tage?

Stufe 1 (#157) verknuepft Erinnerungen und Wissenseintraege semantisch, sobald sie
entstehen. Was fehlte: einmal pro Woche einen Schritt zurueckzutreten und zu fragen,
was das ZUSAMMEN eigentlich bedeutet — welches Thema sich durchzieht, wo Neues einer
aelteren Ueberzeugung widerspricht, was fehlt, und was der groesste Hebel waere.

Drei bewusste Entscheidungen, damit daraus kein zweites System wird:

* **Kein eigener Scheduler.** Der Takt haengt am selben Tick wie die Nachtschicht.
* **Kein eigener Speicher.** Das Ergebnis IST ein Wissenseintrag (``#synthesis``,
  ``#weekly``) — damit taucht es in Suche, Graph und Verknuepfung automatisch auf,
  ohne dass eine Tabelle, eine Migration und eine zweite Anzeige noetig waeren.
* **Keine zweite Aehnlichkeitsrechnung.** Geclustert wird ueber die Verknuepfungen,
  die der Auto-Linker ohnehin schon geschrieben hat (``agent_memory_links``,
  ``brain_links``) — zusammenhaengende Komponenten in diesem Graphen.

Der LLM-Zugang (Anthropic-Key oder Bedrock-Konto) kommt unveraendert aus
``ReflectionService._load_config`` — dieselbe Aufloesung, dieselben Einstellungen.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import resilient_session
from app.models.knowledge import KnowledgeEntry

logger = logging.getLogger(__name__)

SYNTHESIS_TAG = "synthesis"
WEEKLY_TAG = "weekly"
LOOKBACK_DAYS = 7
# Unterhalb davon lohnt der LLM-Aufruf nicht — zwei Notizen ergeben kein Muster.
MIN_ITEMS = 4
# Mehr als das passt weder sinnvoll in einen Prompt noch in einen lesbaren Eintrag.
MAX_ITEMS = 120
MAX_CLUSTERS = 8

_DEFAULT_WEEKDAY = 0   # Montag
_DEFAULT_HOUR = 7

_SYNTHESIS_PROMPT = """Du bist der woechentliche Synthese-Job eines Second Brain.

Unten stehen die Erinnerungen und Wissenseintraege der letzten sieben Tage,
gruppiert nach thematischer Naehe (die Gruppen kommen aus Embedding-Aehnlichkeit,
nicht aus einer Vorsortierung durch einen Menschen).

Analysiere sie und antworte AUSSCHLIESSLICH mit einem JSON-Objekt:

{{
  "muster":        ["..."],   // welches Thema zieht sich durch? 1-3 Punkte
  "widersprueche": ["..."],   // widerspricht Neues einer aelteren Ueberzeugung? 0-3
  "luecken":       ["..."],   // welches Wissen fehlt sichtbar? 0-3
  "aktion":        "...",     // GENAU EINE Sache, groesster Hebel diese Woche
  "titel":         "..."      // kurze Ueberschrift, max 60 Zeichen
}}

Regeln:
- Schreib konkret. "Mehr Struktur noetig" ist wertlos, "Die Preisfrage von Kunde X
  ist dreimal aufgetaucht und nie beantwortet worden" ist brauchbar.
- Behaupte nichts, was nicht in den Daten steht.
- Findest du keinen Widerspruch oder keine Luecke, gib eine leere Liste zurueck —
  erfinde nichts, um die Felder zu fuellen.
- Deutsch, keine Emojis.

DATEN:
{bundle}
"""


def _connected_components(item_ids: list, edges: list[tuple]) -> list[list]:
    """Zusammenhaengende Komponenten — die Cluster.

    Union-Find ueber die vorhandenen Verknuepfungen. Alles ohne Verknuepfung bleibt
    seine eigene Gruppe; das ist gewollt, denn ein Einzelstueck ohne Nachbarn ist
    genau das: ein Thema fuer sich.
    """
    parent = {i: i for i in item_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

    groups: dict = {}
    for i in item_ids:
        groups.setdefault(find(i), []).append(i)
    # Groesste Gruppen zuerst — dort steckt am ehesten ein Muster.
    return sorted(groups.values(), key=len, reverse=True)


class WeeklySynthesisService:
    """Laeuft am selben Tick wie die Nachtschicht, einmal pro Woche."""

    def __init__(self, redis=None):
        self.redis = redis
        self._running = False

    # ------------------------------------------------------------------ tick

    async def tick(self) -> dict | None:
        """Billig, wenn abgeschaltet oder nicht der richtige Wochentag."""
        if self._running:
            return None
        async with resilient_session() as db:
            cfg = await self._load_config(db)
            if not cfg["enabled"]:
                return None
            now_local = datetime.now(ZoneInfo(cfg["tz"]))
            if now_local.weekday() != cfg["weekday"] or now_local.hour != cfg["hour"]:
                return None
            # Schon in dieser Woche gelaufen? Der Wissenseintrag selbst ist die Spur —
            # kein Extra-Zustand, der auseinanderlaufen koennte.
            week_start = (now_local - timedelta(days=now_local.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
            last = await self._latest_synthesis_at(db)
            if last and last >= week_start:
                return None
        return await self.run(trigger="scheduled")

    @staticmethod
    async def _latest_synthesis_at(db: AsyncSession) -> datetime | None:
        """Wann lief die letzte Synthese? Der Wissenseintrag selbst ist die Spur."""
        rows = (await db.execute(
            select(KnowledgeEntry.created_at, KnowledgeEntry.tags)
            .where(KnowledgeEntry.created_by == "synthesis")
            .order_by(KnowledgeEntry.created_at.desc())
            .limit(1)
        )).all()
        for created_at, _tags in rows:
            return created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        return None

    # ------------------------------------------------------------------- run

    async def run(self, trigger: str = "scheduled", user_id: str | None = None) -> dict:
        """Eine Synthese je Nutzer. Wirft nie; Fehler landen im Ergebnis."""
        if self._running:
            return {"skipped": "already running"}
        self._running = True
        try:
            return await self._run_inner(trigger, user_id)
        finally:
            self._running = False

    async def _run_inner(self, trigger: str, only_user: str | None) -> dict:
        result = {"trigger": trigger, "users": 0, "written": 0, "skipped": [], "errors": []}
        async with resilient_session() as db:
            cfg = await self._load_config(db)
            if not cfg.get("backend"):
                result["errors"].append("kein LLM-Zugang (weder Anthropic-Key noch Bedrock-Konto)")
                return result

            user_ids = [only_user] if only_user else await self._users_with_activity(db)
            for uid in user_ids:
                result["users"] += 1
                try:
                    written = await self._synthesize_for_user(db, cfg, uid)
                    if written:
                        result["written"] += 1
                    else:
                        result["skipped"].append(uid)
                except Exception as e:  # noqa: BLE001 — ein Nutzer darf den Lauf nicht kippen
                    logger.warning("[Synthese] Nutzer %s fehlgeschlagen: %s", uid, e)
                    result["errors"].append(str(e)[:200])
            await db.commit()

        if result["written"]:
            await self._notify(result)
        return result

    # -------------------------------------------------------------- sammeln

    async def _users_with_activity(self, db: AsyncSession) -> list[str]:
        since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
        rows = (await db.execute(text(
            "SELECT DISTINCT a.user_id FROM agent_memories m "
            "JOIN agents a ON a.id = m.agent_id "
            "WHERE a.user_id IS NOT NULL AND m.created_at >= :since "
            "  AND m.superseded_by IS NULL"
        ), {"since": since})).all()
        return [r[0] for r in rows if r[0]]

    async def _collect(self, db: AsyncSession, user_id: str) -> tuple[list[dict], list[tuple]]:
        """Erinnerungen + Wissenseintraege der Woche, plus die vorhandenen Kanten.

        Die Schluessel sind praefixiert (``m123`` / ``k45``), damit beide Quellen in
        EINEM Graphen liegen koennen, ohne dass sich die IDs ins Gehege kommen.
        """
        since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

        mem_rows = (await db.execute(text(
            "SELECT m.id, m.content, m.category, m.created_at "
            "FROM agent_memories m JOIN agents a ON a.id = m.agent_id "
            "WHERE a.user_id = :uid AND m.created_at >= :since "
            "  AND m.superseded_by IS NULL "
            "ORDER BY m.importance DESC, m.created_at DESC LIMIT :lim"
        ), {"uid": user_id, "since": since, "lim": MAX_ITEMS})).all()

        know_rows = (await db.execute(
            select(KnowledgeEntry.id, KnowledgeEntry.title, KnowledgeEntry.content,
                   KnowledgeEntry.tags, KnowledgeEntry.updated_at)
            .where(KnowledgeEntry.user_id == user_id, KnowledgeEntry.updated_at >= since)
            .order_by(KnowledgeEntry.updated_at.desc()).limit(MAX_ITEMS)
        )).all()

        items: list[dict] = []
        for mid, content, category, _ in mem_rows:
            items.append({"key": f"m{mid}", "kind": "Erinnerung",
                          "label": category or "", "text": (content or "")[:400]})
        for kid, title, content, tags, _ in know_rows:
            # Fruehere Synthesen nicht wieder mitsynthetisieren — sonst frisst sich
            # das Ergebnis von Woche zu Woche selbst.
            if SYNTHESIS_TAG in (tags or []):
                continue
            items.append({"key": f"k{kid}", "kind": "Wissen",
                          "label": title or "", "text": (content or "")[:400]})

        keys = {i["key"] for i in items}
        edges: list[tuple] = []

        mem_ids = [int(i["key"][1:]) for i in items if i["key"].startswith("m")]
        if mem_ids:
            for src, tgt in (await db.execute(text(
                "SELECT source_id, target_id FROM agent_memory_links "
                "WHERE relation = 'semantic_similar' "
                "  AND source_id = ANY(:ids) AND target_id = ANY(:ids)"
            ), {"ids": mem_ids})).all():
                if f"m{src}" in keys and f"m{tgt}" in keys:
                    edges.append((f"m{src}", f"m{tgt}"))

        know_ids = [int(i["key"][1:]) for i in items if i["key"].startswith("k")]
        if know_ids:
            for src, tgt in (await db.execute(text(
                "SELECT source_id, target_id FROM brain_links "
                "WHERE user_id = :uid AND source_id = ANY(:ids) AND target_id = ANY(:ids)"
            ), {"uid": user_id, "ids": know_ids})).all():
                if f"k{src}" in keys and f"k{tgt}" in keys:
                    edges.append((f"k{src}", f"k{tgt}"))

        return items, edges

    def _bundle(self, items: list[dict], edges: list[tuple]) -> str:
        by_key = {i["key"]: i for i in items}
        clusters = _connected_components([i["key"] for i in items], edges)[:MAX_CLUSTERS]
        lines: list[str] = []
        for n, cluster in enumerate(clusters, 1):
            lines.append(f"\n--- Gruppe {n} ({len(cluster)} Einträge) ---")
            for key in cluster[:20]:
                it = by_key[key]
                label = f" [{it['label']}]" if it["label"] else ""
                lines.append(f"* ({it['kind']}{label}) {it['text']}")
        return "\n".join(lines)

    # ------------------------------------------------------------ schreiben

    async def _synthesize_for_user(self, db: AsyncSession, cfg: dict, user_id: str) -> bool:
        items, edges = await self._collect(db, user_id)
        if len(items) < MIN_ITEMS:
            logger.info("[Synthese] %s: nur %d Eintraege — uebersprungen", user_id, len(items))
            return False

        parsed = await self._ask_llm(cfg, self._bundle(items, edges))
        if not parsed:
            return False

        content = self._render(parsed, len(items), len(edges))
        title = (parsed.get("titel") or "").strip()[:60] or "Wochenrückblick"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        full_title = f"Synthese {stamp} — {title}"

        await self._write_entry(db, user_id, full_title, content)
        return True

    async def _ask_llm(self, cfg: dict, bundle: str) -> dict | None:
        from app.services.reflection_service import ReflectionService
        svc = ReflectionService(self.redis)
        prompt = _SYNTHESIS_PROMPT.format(bundle=bundle)
        try:
            data = (await svc._call_bedrock(cfg, prompt) if cfg["backend"] == "bedrock"
                    else await svc._call_anthropic(cfg, prompt))
            if not data:
                return None
            text_out = (data.get("content") or [{}])[0].get("text") or ""
            start, end = text_out.find("{"), text_out.rfind("}")
            if start < 0 or end <= start:
                return None
            parsed = json.loads(text_out[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception as e:  # noqa: BLE001
            logger.warning("[Synthese] LLM-Aufruf fehlgeschlagen: %s", e)
            return None

    @staticmethod
    def _render(parsed: dict, item_count: int, edge_count: int) -> str:
        def _block(heading: str, values) -> str:
            items = [str(v).strip() for v in (values or []) if str(v).strip()]
            if not items:
                return f"## {heading}\n\nNichts Auffälliges.\n"
            body = "\n".join(f"- {v}" for v in items)
            return f"## {heading}\n\n{body}\n"

        aktion = str(parsed.get("aktion") or "").strip()
        return (
            f"Aus {item_count} Einträgen der letzten {LOOKBACK_DAYS} Tage "
            f"({edge_count} semantische Verbindungen).\n\n"
            + _block("Muster", parsed.get("muster"))
            + "\n" + _block("Widersprüche", parsed.get("widersprueche"))
            + "\n" + _block("Wissenslücken", parsed.get("luecken"))
            + "\n## Die eine Aktion\n\n"
            + (aktion or "Kein klarer Hebel erkennbar.")
            + "\n"
        )[:8000]

    @staticmethod
    async def _write_entry(db: AsyncSession, user_id: str, title: str, content: str) -> None:
        """Als ganz normaler Wissenseintrag, ueber den gemeinsamen Schreibweg.

        Damit liegt die Synthese samt Embedding und Verknuepfungen im selben Graphen
        wie alles andere und laesst sich suchen, verlinken und anzeigen, ohne dass
        dafuer etwas Eigenes gebaut werden muesste.
        """
        from app.core.knowledge_write import write_entry

        await write_entry(
            db, user_id=user_id, title=title, content=content,
            tags=[SYNTHESIS_TAG, WEEKLY_TAG], author="synthesis",
        )

    # --------------------------------------------------------------- melden

    async def _notify(self, result: dict) -> None:
        """Telegram, auf demselben Weg wie die Nachtschicht."""
        try:
            if not settings.telegram_chat_id:
                return
            import redis.asyncio as aioredis
            client = aioredis.from_url(settings.redis_url)
            await client.publish("telegram:notification", json.dumps({
                "text": (f"Wochensynthese fertig — {result['written']} von "
                         f"{result['users']} Second Brain(s) ausgewertet."),
                "chat_id": settings.telegram_chat_id,
            }))
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------- Konfiguration

    async def _load_config(self, db: AsyncSession) -> dict:
        """LLM-Zugang aus der Nachtschicht uebernehmen, Takt eigenstaendig.

        Der Zugang (Anthropic-Key vs. Bedrock-Konto, Modellwahl) wird NICHT noch
        einmal aufgeloest — sonst haetten wir zwei Stellen, die auseinanderlaufen.
        """
        from app.services.reflection_service import ReflectionService
        from app.services.settings_service import SettingsService

        cfg = await ReflectionService(self.redis)._load_config(db)
        svc = SettingsService(db)

        def _int(val, default, lo, hi):
            try:
                return min(hi, max(lo, int(val)))
            except (TypeError, ValueError):
                return default

        enabled = ((await svc.get("synthesis_enabled")) or "").lower() in ("true", "1", "yes")
        cfg["enabled"] = enabled
        cfg["weekday"] = _int(await svc.get("synthesis_weekday"), _DEFAULT_WEEKDAY, 0, 6)
        cfg["hour"] = _int(await svc.get("synthesis_hour"), _DEFAULT_HOUR, 0, 23)
        return cfg
