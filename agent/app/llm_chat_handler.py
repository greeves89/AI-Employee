"""LLM Chat Handler - interactive chat sessions using custom LLM providers."""

import asyncio
import json
import logging
import os
import time

from app import context_compressor, model_registry, multimodal
from app import announcement_guard
from app.loop_detector import LoopDetector
from app.config import settings
from app.ai_credential_status import report_result_status
from app.log_publisher import LogPublisher
from app.providers import create_provider
from app.providers.base import BaseLLMProvider, ChatMessage, LLMEvent, format_exception
from app.tools.definitions import TOOL_DEFINITIONS
from app.tools.executor import ToolExecutor
from app.tools.mcp_client import MCPHTTPClient

logger = logging.getLogger(__name__)

#: Wie viele frueherer Zuege beim Neustart zurueckgeholt werden. Bewusst
#: begrenzt: der Verlauf wandert in den Kontext, und die Kompaktierung greift
#: erst danach. Reicht fuer „worum geht es hier eigentlich", ohne das Fenster
#: schon beim ersten Zug zu fuellen.
VERLAUF_NACHLADEN_MAX = 40

# Tool-loop cap per chat message. Honors the admin-configured
# "Max Turns per Task" setting (settings.max_turns); the constant is
# only a fallback if that is somehow unset. A real agent stops on its
# own long before the cap, and tight repeat-loops are caught separately
# by LoopDetector — so the cap is just a runaway backstop.
DEFAULT_MAX_TURNS = 100


def _max_turns() -> int:
    return settings.max_turns if settings.max_turns and settings.max_turns > 0 else DEFAULT_MAX_TURNS


#: Ab welchem Anteil des Zugbudgets der Agent erfaehrt, wie viel ihm bleibt.
#:
#: Anthropic unterscheidet ausdruecklich zwischen einem Deckel, „the model is
#: not aware of", und einem Budget, mit dem es „paces itself and finishes
#: gracefully instead of being cut off". Bis hierher hatten wir nur den Deckel:
#: die Schleife endete bei ``max_turns`` STILL — kein Wort, keine
#: Zusammenfassung, der Nutzer sah nur abgebrochene Arbeit.
BUDGET_WARNUNG_AB = 0.7

#: Wie oft daran erinnert wird. Jeden Zug waere Laerm; einmal reicht nicht,
#: wenn danach noch zwanzig Zuege kommen.
BUDGET_WARNUNG_ALLE = 10


# --- Lazy tool loading -------------------------------------------------------
# OpenAI/Azure cap function tools at 128 PER REQUEST. Instead of sending the whole
# catalog (18 built-in + 41 orchestrator API + every MCP tool), we send only a small
# CORE set + a `search_tools` meta-tool, and ACTIVATE specific tools on demand when
# the model searches for them. So the catalog can grow without limit.
CORE_TOOL_NAMES = {
    # Delegieren gehoert in den KERN, nicht in den Katalog: was der Agent erst
    # ueber search_tools finden muss, findet er in der Praxis nicht — und redet
    # dann darueber, statt es zu tun.
    "delegate_and_wait", "list_my_team", "list_team_tasks", "get_tasks_status",
    "bash", "read_file", "write_file", "edit_file", "multi_edit",
    "list_files", "grep", "glob", "git_status", "git_diff",
    "web_search", "web_fetch",
    "computer_use",
    # Browser im Container: muss ohne search_tools erreichbar sein, sonst weicht
    # das Modell auf bash/curl aus und bekommt HTML ohne JavaScript-Inhalt.
    "browser",
    "request_approval", "notify_user", "send_message_and_wait",
    "memory_save", "memory_search", "brain_search", "secondbrain_search",
    "list_todos", "complete_todo", "update_todos",
    # Der Tagesplan ist die Antwort auf "was hast du heute vor?" — er muss ohne
    # search_tools erreichbar sein, sonst plant der Agent still in seine Notizdatei.
    "plan_day", "get_day_plan", "complete_onboarding",
    # The standard task workflow MANDATES a skill check + rating on every task, so these
    # must always be loaded — otherwise the agent hits "tool not available" mid-workflow
    # (it cannot search_tools for a capability the workflow already required).
    "skill_search", "skill_install", "skill_rate", "skill_propose", "rate_task",
}
MAX_ACTIVATED_TOOLS = 60  # core (~27) + search_tools + activated stays well under 128
DESKTOP_MCP_ACTIVE_ENV = "AI_EMPLOYEE_DESKTOP_MCP_ACTIVE"


def _core_tool_names() -> set[str]:
    names = set(CORE_TOOL_NAMES)
    if os.environ.get(DESKTOP_MCP_ACTIVE_ENV) == "1":
        names.discard("computer_use")
    return names

# Integration tools that must ALWAYS be sent to the LLM — never hidden behind
# search_tools and never evicted by the LRU. These are the most common M365 asks
# (person/directory lookup, own profile + manager, mail search, recent files); when
# they were only reachable via discovery the model flakily claimed "there is no
# people/M365 tool" instead of calling them. Matched by name SUFFIX so the
# mcp_<server>_ prefix is irrelevant. CORE(~27)+search_tools+pinned(~6)+activated(≤60)
# stays comfortably under the 128 cap.
PINNED_TOOL_SUFFIXES = (
    "ms_search", "ms_search_people", "ms_get_user_info",
    "ms_list_emails", "ms_recent_files", "ms_search_files",
    # Sending mail must be discoverable WITHOUT search_tools — the model otherwise
    # sees only the pinned read tools and wrongly concludes "there is no send tool".
    # Suffix-matched against the agent's OWN catalog, so these only surface when the
    # M365 / on-prem-Exchange integration (write) is actually enabled for the agent.
    "ms_send_email", "ex_send_email",
)


def _is_pinned(name: str) -> bool:
    return any(name.endswith(sfx) for sfx in PINNED_TOOL_SUFFIXES)

SEARCH_TOOLS_DEF = {
    "type": "function",
    "function": {
        "name": "search_tools",
        "description": (
            "Find and load ADDITIONAL tools by capability when none of your currently "
            "available tools fit the task. Searches the full catalog (Microsoft 365 — "
            "mail, calendar, Teams, OneDrive, Planner; the knowledge base; skills; other "
            "integrations) and makes the best matches callable on your NEXT step. Describe "
            "what you want to do, e.g. 'create a folder in OneDrive' or 'send a Teams message'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What you want to do (capability or keywords)."},
            },
            "required": ["query"],
        },
    },
}


def _tokenize(text: str) -> list[str]:
    cleaned = "".join(c if c.isalnum() else " " for c in str(text).lower())
    return cleaned.split()


def _search_catalog(catalog: list[dict], query: str, exclude: set[str], limit: int = 8) -> list[dict]:
    """Keyword-rank tools by query terms over name + description (name hits weigh more)."""
    terms = [t for t in _tokenize(query) if len(t) >= 2]
    if not terms:
        return []
    scored: list[tuple[int, dict]] = []
    for tool in catalog:
        fn = tool.get("function", {})
        name = (fn.get("name") or "")
        if name in exclude:
            continue
        name_l = name.lower()
        hay = name_l + " " + (fn.get("description") or "").lower()
        score = sum(hay.count(term) for term in terms) + sum(2 for term in terms if term in name_l)
        if score > 0:
            scored.append((score, tool))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:limit]]


class LLMChatHandler:
    """Handles interactive chat sessions using custom LLM providers.

    Same interface as ChatHandler — publishes identical events via
    LogPublisher.publish_chat() so the frontend sees no difference.

    Context management: tracks token usage vs an absolute compaction budget
    (context_compressor.effective_threshold_tokens). When exceeded, it runs the
    deterministic compression layers and then a sliding-window rolling summary
    (recent messages kept verbatim, older ones folded into an extending
    summary), then continues seamlessly.
    """

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self.is_running = False
        self._provider: BaseLLMProvider | None = None
        # Modelle, die in dieser Sitzung schon ausgefallen sind (#200).
        self._models_tried: set[str] = set()
        # Wie oft in diesem Lauf schon wegen einer abgerissenen Verbindung
        # wiederholt wurde. Begrenzt, damit ein dauerhaft kaputter Weg nicht
        # still im Kreis laeuft.
        self._connection_retries: int = 0
        self._tool_executor = ToolExecutor()
        self._mcp_client = MCPHTTPClient()
        # Execute MCP tool calls on the same client that ran discovery (shared
        # registry) — otherwise every MCP call fails with "Unknown MCP tool".
        self._tool_executor._mcp_client = self._mcp_client
        self._all_tools: list[dict] | None = None   # full catalog (cached), searchable
        # Tools loaded on demand via search_tools (recency order; capped). Only these
        # plus the CORE set are actually sent to the LLM — keeps us under the 128 cap.
        self._activated: list[str] = []
        # Conversation history (in-memory, replaces --resume)
        self._history: list[ChatMessage] = []
        # Loop detection: track recent tool call signatures
        self._loop_detector = LoopDetector()
        # Context tracking
        self._last_input_tokens: int = 0
        self._context_window: int = 0  # Resolved on first call
        # Measured gap between what the API bills as input and what we can see in
        # the history: the tool schemas (~16k tokens for 60 tools) are sent with
        # every call but live nowhere in _history, and chars/4 underestimates
        # JSON. Without this the trigger and the "did it help" check ran on two
        # different rulers — the trigger saw trouble the check could not find, so
        # the banner appeared turn after turn while nothing was ever compressed.
        self._overhead_tokens: int = 0
        # Size at which a compaction run gave up (overhead dominates, history is
        # already minimal). Retrying that every turn is pure noise.
        self._compaction_floor: int = 0
        # Live steering: async callable returning list[str] of messages that
        # arrived mid-response, to fold into the running conversation.
        self.pending_drain = None
        # Der Nutzer hat Stop gedrueckt. Wir schliessen dabei den laufenden
        # HTTP-Strom — und der schlaegt als httpx-ReadError im Lesen auf. Ohne
        # dieses Merkmal landete unser eigener Abbruch als
        # „Unexpected error: ReadError('')" im Chat: ein Fehler, den niemand
        # gemacht hat.
        self._stopping = False

    def _get_context_window(self) -> int:
        """Resolve the context window size for the current model."""
        if self._context_window > 0:
            return self._context_window
        model = settings.llm_model_name or ""
        self._context_window = model_registry.get_context_window(model)
        known = model_registry.is_known(model)
        logger.info(
            f"[Context] Model '{model}' → window {self._context_window:,}"
            f"{'' if known else ' (unknown model — default)'}, "
            f"compact at {context_compressor.effective_threshold_tokens(self._context_window):,}, "
            f"down to {context_compressor.compaction_target_tokens(self._context_window):,}"
        )
        return self._context_window

    def _note_real_input_tokens(self, reported: int) -> None:
        """Calibrate the estimate against what the API actually billed.

        The difference between the two is the part of the prompt we cannot see
        in the history (tool schemas) plus the tokenizer bias. Keeping the
        largest observed gap makes the estimate lean high — erring towards
        compacting slightly early, never towards a hard context overflow.
        """
        if reported <= 0:
            return
        self._last_input_tokens = reported
        gap = reported - context_compressor.estimate_tokens(self._history)
        if gap > self._overhead_tokens:
            self._overhead_tokens = gap

    def _estimate_tokens(self) -> int:
        """Projected prompt size: what we can measure plus the measured overhead.

        ONE ruler for the trigger and for the check afterwards. Reading the raw
        API count for the trigger and a history-only estimate for the check is
        what produced compaction runs that announced themselves and then had
        nothing to do.
        """
        return context_compressor.estimate_tokens(self._history) + self._overhead_tokens


    async def _heal_after_context_overflow(self, message_id: str, error_text: str) -> None:
        """Fallnetz zu #623: Kontextueberlauf heilt sich fuer den NAECHSTEN Zug.

        Der praeventive Kompaktierer (siehe _needs_compaction) verhindert das
        normalerweise — schlaegt trotzdem ein Zug mit Kontextlaengen-Fehler auf,
        wird der Verlauf sofort komprimiert und der Nutzer informiert, statt
        dass jede weitere Nachricht identisch scheitert (Symptom #613).
        """
        from app.chat_handler import _is_context_length_error
        if not _is_context_length_error(error_text):
            return
        try:
            await self._compact_history(message_id)
            await self.log_publisher.publish_chat(
                message_id, "system",
                {"message": "Der Gespraechsverlauf war zu lang geworden — ich habe "
                            "ihn komprimiert. Bitte schicke deine Nachricht noch "
                            "einmal; Details aus fruehen Nachrichten kenne ich nur "
                            "noch zusammengefasst."},
            )
        except Exception:  # noqa: BLE001
            logger.warning("Notkompaktierung nach Kontextueberlauf fehlgeschlagen", exc_info=True)

    def _needs_compaction(self) -> bool:
        """Check if the conversation needs compaction."""
        if len(self._history) < 6:
            return False  # Too short to compact
        estimated = self._estimate_tokens()
        window = self._get_context_window()
        threshold = context_compressor.effective_threshold_tokens(window)
        if estimated < threshold:
            return False
        # A previous run could not get below the target. Nothing changed enough
        # since then for a second attempt to end differently.
        if self._compaction_floor:
            growth = context_compressor.reattempt_growth_tokens(window)
            if estimated < self._compaction_floor + growth:
                return False
        logger.info(
            f"[Context] {estimated:,} tokens ≥ {threshold:,} threshold "
            f"(window {window:,}) — compaction needed"
        )
        return True

    async def _compact_history(self, message_id: str) -> None:
        """4-layer context compression pipeline.

        Layer 1–3 are deterministic and run first (fast, no LLM call).
        Layer 4 (LLM summarization) is only invoked if still above the target.

        The user is told AFTERWARDS, and only if something actually shrank.
        Announcing up front meant a run that turned out to be a no-op still put
        "[Kontext wird komprimiert...]" in the chat — the visible half of the
        "compacts too often" complaint was largely this: banners without work.
        """
        provider = self._get_provider()
        window = self._get_context_window()
        target = context_compressor.compaction_target_tokens(window)
        before = self._estimate_tokens()
        # The layers only ever see the history; the overhead is not theirs to cut.
        history_target = max(1, target - self._overhead_tokens)

        # Layers 1–3: Snip → Microcompact → Collapse (deterministic)
        compressed, applied = context_compressor.compress_messages(
            self._history, history_target
        )
        if applied:
            self._history = compressed
            logger.info(f"[Context] Deterministic layers {applied} applied")

        # Layer 4: LLM-based abstractive summarization (last resort)
        if self._estimate_tokens() > target:
            old_count = len(self._history)
            new_history = await context_compressor.summarize_messages(self._history, provider)
            if new_history:
                self._history = new_history
                logger.info(
                    f"[Context] Layer 4 summarized {old_count} → {len(self._history)} msgs"
                )
            else:
                logger.info(
                    "[Context] nothing old enough to summarize — the fixed overhead "
                    "dominates; not retrying until the context grows"
                )

        after = self._estimate_tokens()
        self._last_input_tokens = 0
        # Remember a run that could not reach the target, so the next turn does
        # not repeat it for nothing.
        self._compaction_floor = 0 if after <= target else after

        if after < before:
            await self.log_publisher.publish_chat(
                message_id, "text",
                {"text": f"\n\n`[Kontext verdichtet: {before // 1000}k → {after // 1000}k Token]`\n\n"},
            )
        else:
            logger.info(f"[Context] compaction had no effect at {after:,} tokens")

    async def _get_catalog(self) -> list[dict]:
        """Full tool catalog (built-in + orchestrator API + MCP), cached. This is the
        SEARCHABLE set — not everything here is sent to the LLM."""
        if self._all_tools is not None:
            return self._all_tools
        catalog = list(TOOL_DEFINITIONS)
        try:
            mcp_tools = await self._mcp_client.discover_tools()
            if mcp_tools:
                catalog.extend(mcp_tools)
                logger.info(f"Discovered {len(mcp_tools)} MCP tools (catalog size {len(catalog)})")
        except Exception as e:
            logger.warning(f"MCP tool discovery failed: {e}")
        self._all_tools = catalog
        # Pre-activate the agent's integration MCP tools (M365/msgraph, Exchange, …)
        # so they are ALWAYS callable. Without this they're only reachable via
        # search_tools, and the model unreliably claims "no M365 tool available"
        # instead of searching (the "mal da / mal nicht" flakiness). Capped to
        # leave headroom for on-demand search_tools activations under the 128 limit.
        if not self._activated:
            core_names = _core_tool_names()
            mcp_names = [
                t["function"]["name"] for t in catalog
                if str(t.get("function", {}).get("name", "")).startswith("mcp_")
                and t["function"]["name"] not in core_names
            ]
            if mcp_names:
                self._activated = mcp_names[: max(1, MAX_ACTIVATED_TOOLS - 15)]
                logger.info(f"Pre-activated {len(self._activated)} integration MCP tools")
        return catalog

    async def _get_tools(self) -> list[dict] | None:
        """Tools actually SENT to the LLM this turn: the CORE set + search_tools +
        whatever was activated via search_tools so far. The rest of the catalog is
        reachable only by searching for it — that's what keeps us under the 128 cap."""
        if not settings.llm_tools_enabled:
            return None
        catalog = await self._get_catalog()
        active = set(self._activated)
        core_names = _core_tool_names()
        sent = [t for t in catalog if t["function"]["name"] in core_names]
        sent.append(SEARCH_TOOLS_DEF)
        sent_names = {t["function"]["name"] for t in sent}
        # Always-pinned integration tools (people/mail/search) — no discovery needed.
        for t in catalog:
            n = t["function"]["name"]
            if n not in sent_names and n not in core_names and _is_pinned(n):
                sent.append(t)
                sent_names.add(n)
        # Everything the model activated via search_tools (minus what's already sent).
        sent += [t for t in catalog
                 if t["function"]["name"] in active and t["function"]["name"] not in sent_names]
        return sent

    def _handle_search_tools(self, query: str) -> str:
        """Search the catalog and activate the best matches for the next turn."""
        matches = _search_catalog(self._all_tools or [], query, _core_tool_names() | {"search_tools"})
        if not matches:
            return f"Keine passenden Tools für '{query}' gefunden. Versuch andere Stichwörter."
        lines = []
        for tool in matches:
            name = tool["function"]["name"]
            # Move-to-end (recency) + dedup
            if name in self._activated:
                self._activated.remove(name)
            self._activated.append(name)
            lines.append(f"- {name}: {(tool['function'].get('description') or '')[:160]}")
        # LRU cap so core + activated never approaches the 128 limit
        if len(self._activated) > MAX_ACTIVATED_TOOLS:
            self._activated = self._activated[-MAX_ACTIVATED_TOOLS:]
        return "Folgende Tools sind ab deinem nächsten Schritt aufrufbar:\n" + "\n".join(lines)

    async def _retry_after_connection_glitch(self, task_id: str, error_text: str | None) -> bool:
        """Abgerissene Verbindung: DENSELBEN Aufruf noch einmal, kurz gewartet.

        Ein Modellwechsel waere hier die falsche Antwort — das Modell ist in
        Ordnung, die Leitung war es nicht. Und ohne gefuellte Ausweichkette
        (Regelfall) haette der Wechsel ohnehin nichts zu wechseln: der Lauf starb
        an einem einzigen abgerissenen Lesevorgang, nach 40 Zuegen Arbeit.

        Hoechstens zwei Versuche. Reisst es dreimal, liegt es nicht am Zufall,
        und stilles Weiterprobieren wuerde nur den echten Grund verdecken.
        """
        from app import model_fallback

        if not model_fallback.is_connection_glitch(error_text):
            return False
        if self._connection_retries >= 2:
            return False
        self._connection_retries += 1
        wartezeit = 2 * self._connection_retries
        logger.warning(
            "[Verbindung] abgerissen (%s) — Versuch %d/2 in %ds",
            (error_text or "")[:120], self._connection_retries, wartezeit,
        )
        await self.log_publisher.publish(
            task_id, "system",
            {"message": f"[Verbindung abgerissen — neuer Versuch {self._connection_retries}/2]"},
        )
        await asyncio.sleep(wartezeit)
        return True

    async def _switch_to_fallback(self, message_id: str, error_text: str | None) -> bool:
        """Auf das nächste Ausweichmodell umstellen (#200) — wie im Auftragslauf.

        Bewusst dieselbe Entscheidung aus ``model_fallback``: Kapazitätsprobleme
        weichen aus, Einrichtungsfehler scheitern sofort. Zwei Auslegungen davon
        wären genau die Sorte Doppelpflege, die hier schon mehrfach zugeschlagen hat.
        """
        from app import model_fallback

        if not model_fallback.is_retryable(error_text):
            return False
        chain = model_fallback.parse_chain(settings.llm_fallback_models)
        target = model_fallback.next_model(
            settings.llm_model_name, chain, self._models_tried
        )
        if not target:
            return False

        previous = settings.llm_model_name
        self._models_tried.add(previous)
        settings.llm_model_name = target
        if self._provider:
            try:
                await self._provider.close()
            except Exception:  # noqa: BLE001
                logger.debug("Alter Provider liess sich nicht schliessen", exc_info=True)
        self._provider = None

        logger.warning("[Modell] %s antwortete nicht (%s) — weiter mit %s",
                       previous, (error_text or "")[:120], target)
        await self.log_publisher.publish_chat(
            message_id, "system",
            {"message": f"[Modellwechsel: {previous} → {target}]"},
        )
        return True

    def _get_provider(self) -> BaseLLMProvider:
        if not self._provider:
            self._provider = create_provider(
                provider_type=settings.llm_provider_type,
                api_endpoint=settings.llm_api_endpoint,
                api_key=settings.llm_api_key,
                model_name=settings.llm_model_name,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
                thinking_mode=settings.llm_thinking_mode,
                reasoning_effort=settings.llm_reasoning_effort,
                api_version=settings.llm_api_version,
            )
        return self._provider

    async def _abschluss_erbitten(self, message_id: str, provider, zuege: int) -> str:
        """Einen letzten Zug OHNE Werkzeuge: was ist fertig, was bleibt offen.

        Ohne Werkzeuge, weil der Agent sonst weiterarbeitet statt abzuschliessen
        — und genau das Budget ist ja gerade der Grund, warum er aufhoeren soll.

        Best effort: schlaegt der Abschluss fehl, bleibt es beim bisherigen
        Text. Ein Fehler HIER darf die geleistete Arbeit nicht auch noch
        verschlucken.
        """
        self._history.append(ChatMessage(
            role="system",
            content=(
                f"Dein Arbeitsbudget fuer diese Aufgabe ist aufgebraucht ({zuege} Schritte). "
                "Schliesse jetzt ab, ohne weitere Werkzeuge zu benutzen. Sage in wenigen "
                "Saetzen: was ist FERTIG, was ist OFFEN, und was waere der naechste Schritt. "
                "Wenn die Aufgabe zu gross war, sag das und schlage vor, wie man sie teilt."
            ),
        ))
        try:
            text = ""
            async for event in provider.stream_completion(self._history, []):
                if event.type == "text_delta":
                    text += event.text
                    await self.log_publisher.publish_chat(message_id, "text", {"text": event.text})
            return text.strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Chat] Abschluss nach Budgetende fehlgeschlagen: %s", e)
            return ("\n\n[System: Das Arbeitsbudget war aufgebraucht, bevor die Aufgabe "
                    "fertig wurde. Bitte grenze sie enger ein oder teile sie auf.]")

    async def _verlauf_nachladen(self) -> list[ChatMessage]:
        """Die letzten Zuege dieser Unterhaltung aus dem Orchestrator holen.

        Best effort: geht es schief, redet der Agent ohne Vorgeschichte weiter —
        das ist der Zustand von vorher und darf den Zug nicht kippen.
        """
        from app.tools.api_client import current_chat_session

        session_id = current_chat_session.get(None)
        if not session_id:
            return []
        try:
            from app.tools.api_client import OrchestratorAPIClient
            client = OrchestratorAPIClient()
            try:
                antwort = await client._request(
                    "GET",
                    f"/agents/{client.agent_id}/chat/history",
                    params={"session_id": session_id, "limit": VERLAUF_NACHLADEN_MAX},
                )
            finally:
                await client._client.aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Kontext] Verlauf nicht nachladbar: %s", e)
            return []

        if not isinstance(antwort, dict):
            logger.warning("[Kontext] Verlauf nicht nachladbar: %s", str(antwort)[:200])
            return []

        zuege: list[ChatMessage] = []
        for eintrag in antwort.get("messages") or []:
            rolle = eintrag.get("role")
            inhalt = (eintrag.get("content") or "").strip()
            # Nur echte Wortmeldungen. `system`-Zeilen sind Kacheln und
            # Statusmeldungen der Oberflaeche — sie standen dem Modell nie zur
            # Verfuegung und wuerden es jetzt nur verwirren.
            if rolle in ("user", "assistant") and inhalt:
                zuege.append(ChatMessage(role=rolle, content=inhalt))

        if zuege:
            logger.info("[Kontext] %d fruehere Zuege dieser Unterhaltung geladen", len(zuege))
        return zuege

    async def handle_message(
        self,
        message_id: str,
        text: str,
        model: str | None = None,
        images: list[dict] | None = None,
        reasoning: str = "",
    ) -> dict:
        """Process a chat message with the custom LLM provider.

        ``images`` is an optional list of ``{"media_type", "data"}`` dicts
        (base64) — e.g. a photo pasted in the Web UI or sent via Telegram.
        Multimodal models see them directly.
        """
        self.is_running = True
        self._stopping = False
        start_time = time.time()
        provider = self._get_provider()
        # The provider is cached across turns, so a per-message choice has to be
        # written onto it each turn (and an explicit "off" has to clear the
        # container default, not fall through to it). "max" becomes "xhigh"; the
        # provider clamps it to "high" for models that don't know xhigh.
        if reasoning:
            provider.reasoning_effort = {"off": "", "max": "xhigh"}.get(reasoning, reasoning)
        else:
            from app.config import llm_default_reasoning_effort
            provider.reasoning_effort = llm_default_reasoning_effort()

        # Build system message if this is the first message
        if not self._history:
            from app.runner_hooks import (
                MULTIMODAL_CAPABILITY_NOTE,
                get_identity_context,
                get_memory_preload,
                get_skills_context,
                get_mounts_context,
                get_marketplace_skill_suggestions,
            )
            system_prompt = settings.llm_system_prompt or (
                "You are a helpful AI coding assistant running in a Docker container. "
                "Your workspace is at /workspace. Use the available tools to help the user."
            )
            # WHO the agent is (name, role, its AGENT.md) and WHAT it already knows — the
            # CLI runtimes read both from disk, this runtime has to be handed them. Without
            # it the agent answers "ich habe keinen eigenen Namen" and forgets across
            # sessions what the user told it, although it saved it.
            system_prompt = system_prompt + get_identity_context() + get_memory_preload(text[:500])
            system_prompt = system_prompt + MULTIMODAL_CAPABILITY_NOTE
            system_prompt = system_prompt + (
                "\n\n## Werkzeuge bei Bedarf nachladen\n"
                "Dir ist nur ein KERN-Satz an Werkzeugen direkt verfügbar. Für alles "
                "Weitere — Microsoft 365 (Mail, Kalender, Teams, OneDrive, Planner), "
                "Wissensdatenbank, Skills, weitere Integrationen — rufe ZUERST "
                "`search_tools` mit einer Beschreibung der gewünschten Aktion auf "
                "(z. B. 'Ordner in OneDrive anlegen'); die passenden Werkzeuge sind "
                "dann ab deinem nächsten Schritt aufrufbar."
            )
            # Host mounts / Second Brain awareness + marketplace skills — parity with
            # the task runtimes so chat agents also search the shared vault and skills.
            system_prompt = system_prompt + get_mounts_context()
            skills_ctx = get_skills_context()
            if skills_ctx:
                system_prompt = system_prompt + "\n" + skills_ctx
            marketplace = get_marketplace_skill_suggestions(text[:200])
            if marketplace:
                system_prompt = system_prompt + "\n" + marketplace
            self._history.append(ChatMessage(role="system", content=system_prompt))

            # Den bisherigen Verlauf DIESER Unterhaltung zurueckholen.
            #
            # Bei dieser Laufzeit lebt der Verlauf ausschliesslich im
            # Arbeitsspeicher — anders als bei den CLI-Laufzeiten, die ihre
            # Sitzung ueber `--resume` wiederfinden. Nach jedem Neustart,
            # Update oder Container-Tausch stand der Agent in einem Chat mit 70
            # gespeicherten Nachrichten vor einem leeren Blatt und musste sich
            # aus semantisch gesuchten Erinnerungen zusammenreimen, worum es
            # geht. Beim Kunden am 18.08.2026 riet er daraufhin das falsche
            # Projekt und schickte vier Kollegen darauf los.
            #
            # Die Erinnerungen bleiben — sie tragen Wissen ueber Unterhaltungen
            # hinweg. Sie sind nur nicht mehr die einzige Quelle.
            geladen = await self._verlauf_nachladen()
            if geladen:
                self._history.extend(geladen)

        # Add user message to history (image-aware)
        self._history.append(multimodal.user_message(text, images))

        # Context compaction: if approaching context limit, summarize first
        if self._needs_compaction():
            await self._compact_history(message_id)

        # Reset loop detector for this message
        self._loop_detector.reset()
        # Ein Anstupser je Nachricht des Menschen — siehe unten.
        ansporn_offen = True

        tools = await self._get_tools()
        full_text = ""
        accumulated_tool_calls: list[dict] = []
        num_turns = 0
        max_turns = _max_turns()
        total_input_tokens = 0
        total_output_tokens = 0
        total_reasoning_tokens = 0
        total_cached_tokens = 0
        total_cache_write_tokens = 0

        try:
            letzte_warnung = 0
            while num_turns < max_turns:
                num_turns += 1

                # Budget-Bewusstsein: dem Agenten SAGEN, wie viel ihm bleibt,
                # statt ihn irgendwann abzuschneiden. Ein Modell, das sein
                # Budget kennt, teilt sich ein und liefert einen brauchbaren
                # Zwischenstand; eines, das es nicht kennt, wird mitten im Satz
                # gekappt.
                rest = max_turns - num_turns
                if (num_turns >= max_turns * BUDGET_WARNUNG_AB
                        and num_turns - letzte_warnung >= BUDGET_WARNUNG_ALLE):
                    letzte_warnung = num_turns
                    self._history.append(ChatMessage(
                        role="system",
                        content=(
                            f"Hinweis zum Arbeitsbudget: dir bleiben noch etwa {rest} Schritte "
                            "fuer diese Aufgabe. Teile sie dir ein. Wenn es knapp wird, bring "
                            "zuerst das Wichtigste zu Ende und fasse dann zusammen, was erledigt "
                            "ist und was offen bleibt — statt mitten in der Arbeit zu enden."
                        ),
                    ))
                # Re-fetch each turn so tools activated via search_tools on the
                # previous turn become callable now (lazy loading).
                tools = await self._get_tools()
                has_tool_calls = False
                turn_text = ""
                turn_tool_calls: list[dict] = []
                tools_dieser_zug: set[str] = set()
                switched_model = False

                async for event in provider.stream_completion(self._history, tools):
                    if event.type == "text_delta":
                        turn_text += event.text
                        full_text += event.text
                        await self.log_publisher.publish_chat(
                            message_id, "text", {"text": event.text}
                        )

                    elif event.type == "tool_call":
                        has_tool_calls = True
                        turn_tool_calls.append({
                            "id": event.tool_id,
                            "name": event.tool_name,
                            "input": event.tool_input,
                        })
                        accumulated_tool_calls.append({
                            "tool": event.tool_name,
                            "input": json.dumps(event.tool_input)[:200],
                        })
                        await self.log_publisher.publish_chat(
                            message_id, "tool_call",
                            {
                                "tool_use_id": event.tool_id,
                                "tool": event.tool_name,
                                "input": event.tool_input,
                            },
                        )

                    elif event.type == "done":
                        # Track actual token usage from API for context monitoring.
                        # Calibrates the estimate: the history we can measure vs
                        # the prompt the API actually billed.
                        self._note_real_input_tokens(event.input_tokens or 0)
                        # Each turn is a separately-billed API call — sum every
                        # turn's tokens for the message's total cost.
                        total_input_tokens += event.input_tokens or 0
                        total_output_tokens += event.output_tokens or 0
                        total_reasoning_tokens += getattr(event, "reasoning_tokens", 0) or 0
                        total_cached_tokens += getattr(event, "cached_tokens", 0) or 0
                        total_cache_write_tokens += getattr(event, "cache_write_tokens", 0) or 0

                    elif event.type == "error":
                        if self._stopping:
                            return await self._finish_cancelled(message_id, start_time, num_turns)
                        # Gleiche Ausfallsicherheit wie im Auftragslauf (#200) —
                        # im Chat merkt der Mensch einen Ausfall sofort, hier ist
                        # sie also eher wichtiger als dort.
                        # Erst der billige Fall: abgerissene Verbindung, derselbe
                        # Aufruf noch einmal. Siehe llm_runner — gleiche Regel in
                        # beiden Laufzeiten, sonst ist der Chat schlechter dran.
                        if await self._retry_after_connection_glitch(message_id, event.text):
                            switched_model = True   # Merker heisst „Zug wiederholen"
                            provider = self._get_provider()
                            break
                        if await self._switch_to_fallback(message_id, event.text):
                            switched_model = True
                            provider = self._get_provider()
                            break
                        await self.log_publisher.publish_chat(
                            message_id, "error", {"message": event.text}
                        )
                        self.is_running = False
                        result = {"status": "error", "error": event.text}
                        await self._heal_after_context_overflow(message_id, event.text)
                        # Ein abgelaufener eigener Zugang muss auch HIER sichtbar
                        # werden. Der Aufgaben-Weg meldet ihn seit #660; der
                        # Chat-Weg des eigenen Modells war der letzte, der schwieg —
                        # wer sein Modell nur im Chat nutzt, sah nie einen Hinweis.
                        await report_result_status(result)
                        await self.log_publisher.publish_chat(message_id, "done", result)
                        return result

                if switched_model:
                    # Zug wiederholen (anderes Modell ODER dasselbe nach einer
                    # abgerissenen Verbindung) — es gab keine verwertbare
                    # Antwort, die in den Verlauf gehoerte.
                    continue

                # Add assistant response to history
                if turn_text and not turn_tool_calls:
                    self._history.append(ChatMessage(role="assistant", content=turn_text))
                elif turn_tool_calls:
                    tool_calls_content = [{
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])},
                    } for tc in turn_tool_calls]
                    self._history.append(ChatMessage(
                        role="assistant",
                        content=turn_text or None,
                        tool_calls=tool_calls_content,
                    ))

                if not has_tool_calls:
                    # Live steering: before finishing, fold in any messages
                    # that arrived while we were responding — same conversation.
                    if self.pending_drain is not None:
                        extra = await self.pending_drain()
                        if extra:
                            for t in extra:
                                self._history.append(ChatMessage(role="user", content=t))
                                full_text += f"\n\n[Neue Nachricht aufgenommen]\n"
                            await self.log_publisher.publish_chat(
                                message_id, "system",
                                {"message": f"{len(extra)} neue Nachricht(en) aufgenommen — wird mitverarbeitet."},
                            )
                            max_turns = num_turns + _max_turns()
                            continue

                    # „Ich mache das jetzt" — und dann nichts.
                    #
                    # Im Auftrags-Pfad ist das seit v1.178.2 abgesichert; der
                    # Chat hatte die Pruefung nie, und die Sprachfront laeuft
                    # ueber den Chat. Am 2026-08-16 sagte ein Agent per Sprache
                    # zweimal zu, eine App JETZT zu bauen, und tat beide Male
                    # nichts — erst auf „Hast du die App gebaut!!!???" sah er
                    # nach und gab zu, dass nichts existiert.
                    #
                    # Bewusst enger als beim Auftrag: im Chat ist Reden der
                    # Normalfall. Ausloeser ist nicht die fehlende Arbeit,
                    # sondern der WIDERSPRUCH zwischen Zusage und Untaetigkeit.
                    # Genau einmal je Zug — ein zweiter Anstupser waere
                    # Bevormundung, wenn der Agent begruendet ablehnt.
                    if ansporn_offen and announcement_guard.promises_but_does_nothing(
                        turn_text, tools_dieser_zug
                    ):
                        ansporn_offen = False
                        logger.info("[Chat] Zusage ohne Handlung — Anstupser")
                        self._history.append(ChatMessage(
                            role="user", content=announcement_guard.NUDGE,
                        ))
                        max_turns = num_turns + 4
                        continue
                    break

                # Loop detection: check for repetitive tool call patterns
                for tc in turn_tool_calls:
                    tools_dieser_zug.add(tc["name"])
                    self._loop_detector.record(tc["name"], tc["input"])
                if self._loop_detector.is_looping():
                    loop_msg = (
                        "Loop detected: the same tool calls are repeating. "
                        "Stopping to prevent runaway execution."
                    )
                    logger.warning(f"[Chat] {loop_msg}")
                    full_text += f"\n\n[System: {loop_msg}]"
                    await self.log_publisher.publish_chat(
                        message_id, "text", {"text": f"\n\n[System: {loop_msg}]"}
                    )
                    break

                # Execute tool calls — parallelize read-only, serialize writes
                from app.tools.executor import _CACHEABLE_TOOLS
                _WRITE_TOOLS = {"write_file", "edit_file", "multi_edit", "bash"}

                results_map: dict[str, str] = {}
                # search_tools is handled in-handler (it owns the catalog + activation
                # set) and never reaches the executor — it loads tools for the next turn.
                for tc in turn_tool_calls:
                    if tc["name"] == "search_tools":
                        results_map[tc["id"]] = self._handle_search_tools(tc["input"].get("query", ""))
                _dispatch = [tc for tc in turn_tool_calls if tc["name"] != "search_tools"]

                read_only = [tc for tc in _dispatch if tc["name"] in _CACHEABLE_TOOLS]
                write_ops = [tc for tc in _dispatch if tc["name"] in _WRITE_TOOLS]
                other_ops = [tc for tc in _dispatch if tc not in read_only and tc not in write_ops]
                if read_only:
                    parallel_results = await asyncio.gather(
                        *[self._tool_executor.execute(tc["name"], tc["input"]) for tc in read_only],
                        return_exceptions=True,
                    )
                    for tc, res in zip(read_only, parallel_results):
                        results_map[tc["id"]] = str(res) if isinstance(res, Exception) else res

                for tc in other_ops + write_ops:
                    results_map[tc["id"]] = await self._tool_executor.execute(tc["name"], tc["input"])

                for tc in turn_tool_calls:
                    result_text = results_map[tc["id"]]
                    # present_image: stream the actual image to the chat UI
                    if tc["name"] == "present_image" and multimodal.is_image_result(result_text):
                        payload = multimodal.parse_image_result(result_text) or {}
                        if payload.get("data"):
                            await self.log_publisher.publish_chat(
                                message_id, "image",
                                {
                                    "media_type": payload.get("media_type", "image/png"),
                                    "data": payload["data"],
                                    "caption": payload.get("note", ""),
                                },
                            )
                    if tc["name"] == "present_file" and result_text.startswith("__AI_EMPLOYEE_PRESENT_FILE__"):
                        try:
                            payload = json.loads(result_text.removeprefix("__AI_EMPLOYEE_PRESENT_FILE__"))
                            await self.log_publisher.publish_chat(message_id, "file", payload)
                        except Exception:
                            pass
                    await self.log_publisher.publish_chat(
                        message_id, "tool_result",
                        {
                            "tool_use_id": tc["id"],
                            "content": "File presented to the user."
                            if result_text.startswith("__AI_EMPLOYEE_PRESENT_FILE__")
                            else multimodal.log_summary(result_text),
                        },
                    )
                    self._history.append(
                        multimodal.tool_message(result_text, tc["id"], tc["name"])
                    )

                # Mid-turn compaction: if a tool-heavy turn filled the context
                if self._needs_compaction():
                    await self._compact_history(message_id)

                # Live steering (mid-turn): fold in any messages that arrived
                # while the tools were running, so the agent picks up the new
                # info on its very NEXT step — not only at the end of the turn.
                # Drained AFTER compaction so fresh input is never summarized away.
                if self.pending_drain is not None:
                    extra = await self.pending_drain()
                    if extra:
                        for t in extra:
                            self._history.append(ChatMessage(role="user", content=t))
                            full_text += "\n\n[Neue Nachricht aufgenommen]\n"
                        await self.log_publisher.publish_chat(
                            message_id, "system",
                            {"message": f"{len(extra)} neue Nachricht(en) aufgenommen — wird sofort mitverarbeitet."},
                        )
                        # New input extends the work budget for this message.
                        max_turns = num_turns + _max_turns()

            else:
                # Das Zugbudget ist aufgebraucht, ohne dass der Agent fertig
                # wurde. Bis hierher endete die Schleife hier STILL — der
                # Nutzer bekam abgebrochene Arbeit ohne Hinweis und ohne zu
                # wissen, was erledigt ist.
                #
                # OpenAI empfiehlt fuer genau diesen Fall einen Behandler, der
                # den Lauf sauber beendet („I couldn't finish within the turn
                # limit. Please narrow the request."). Statt eines festen
                # Satzes lassen wir den Agenten selbst zusammenfassen — er
                # weiss als Einziger, was er geschafft hat.
                logger.info("[Chat] Zugbudget (%d) erschoepft — Abschluss wird erbeten", max_turns)
                abschluss = await self._abschluss_erbitten(message_id, provider, num_turns)
                if abschluss:
                    full_text += "\n\n" + abschluss

        except Exception as e:
            # Vom Nutzer abgebrochen: kein Fehler, sondern das gewuenschte Ergebnis.
            if self._stopping:
                logger.info("LLM Chat vom Nutzer angehalten (%s)", type(e).__name__)
                return await self._finish_cancelled(message_id, start_time, num_turns)
            logger.exception(f"LLM Chat error: {e}")
            # format_exception() prefixes the exception TYPE (matches the
            # provider-level error style) — a bare str(e) like the previous
            # version left a customer-reported chat error self-diagnosing
            # only from the container log, and that log is gone the moment
            # the agent gets recreated (Rueckmeldung beim Kunden, 2026-08-28).
            failure_text = format_exception(e)
            await self.log_publisher.publish_chat(
                message_id, "error", {"message": failure_text}
            )
            self.is_running = False
            result = {"status": "error", "error": failure_text}
            await self._heal_after_context_overflow(message_id, failure_text)
            await report_result_status(result)
            await self.log_publisher.publish_chat(message_id, "done", result)
            return result

        duration_ms = int((time.time() - start_time) * 1000)
        from app.llm_runner import _estimate_cost
        result = {
            "status": "completed",
            "text": full_text,
            "duration_ms": duration_ms,
            "num_turns": num_turns,
            "cost_usd": _estimate_cost(
                settings.llm_model_name, total_input_tokens, total_output_tokens
            ),
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "reasoning_tokens": total_reasoning_tokens,
            "cached_tokens": total_cached_tokens,
            "cache_write_tokens": total_cache_write_tokens,
            "tool_calls": accumulated_tool_calls or None,
        }

        self.is_running = False
        await report_result_status(result)
        await self.log_publisher.publish_chat(message_id, "done", result)
        return result

    async def _finish_cancelled(self, message_id: str, start_time: float, num_turns: int) -> dict:
        """Sauberer Abschluss nach einem Abbruch durch den Nutzer.

        Der Verlauf bleibt stehen, wie er ist — abgebrochen heisst angehalten,
        nicht verworfen. Der naechste Zug setzt darauf auf.
        """
        self.is_running = False
        self._stopping = False
        result = {
            "status": "cancelled",
            "duration_ms": int((time.time() - start_time) * 1000),
            "num_turns": num_turns,
        }
        await self.log_publisher.publish_chat(message_id, "cancelled", result)
        await self.log_publisher.publish_chat(message_id, "done", result)
        return result

    async def stop_current(self) -> None:
        """Stop the currently running request."""
        self._stopping = True
        self.is_running = False
        if self._provider:
            await self._provider.close()
            self._provider = None

    async def reset_session(self) -> None:
        """Reset conversation history."""
        self._history.clear()
        self._loop_detector.reset()
        self._last_input_tokens = 0
        self._compaction_floor = 0
        # The overhead survives on purpose: tool schemas and tokenizer bias do
        # not change when the conversation does, and a calibration already paid
        # for should not have to be re-learned.
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Chat session reset"}
        )
