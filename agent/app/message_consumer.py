"""Message consumer - handles inter-agent messages.

Listens on a dedicated Redis queue (agent:{id}:messages) for messages from
other agents. Processes each message via Claude CLI and automatically sends
the response back to the sender via the orchestrator API.
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis

from app.config import get_oauth_token, settings
from app.log_publisher import LogPublisher

logger = logging.getLogger(__name__)


class MessageConsumer:
    """Consumes inter-agent messages and auto-replies."""

    # Rate limit: max messages per agent pair per hour
    MAX_REPLIES_PER_PAIR = 5

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:messages"
        self.running = True
        self._process: asyncio.subprocess.Process | None = None
        self._reply_counts: dict[str, int] = {}  # agent_id -> count this hour
        self._reply_reset_at: float = 0

    async def _get_conversation_history(self, other_agent_id: str) -> str:
        """Load recent conversation history with another agent from orchestrator API."""
        import urllib.request
        import urllib.error

        url = (
            f"{settings.orchestrator_url}/api/v1/agents/team/conversation"
            f"?agent_a={self.agent_id}&agent_b={other_agent_id}"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {settings.agent_token}",
                    "X-Agent-ID": self.agent_id,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                messages = data.get("messages", [])
                if not messages:
                    return "(Keine bisherigen Nachrichten)"
                # Format last 10 messages
                lines = []
                for msg in messages[-10:]:
                    lines.append(f"[{msg['from_name']}]: {msg['text'][:200]}")
                return "\n".join(lines)
        except Exception as e:
            logger.debug(f"Could not load conversation history: {e}")
            return "(Verlauf nicht verfügbar)"

    async def _execute_cli(self, prompt: str, model: str | None = None) -> str:
        """Run Claude CLI with the prompt and return the text response."""
        import os

        cmd = ["claude", "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"]
        if model and model != "default":
            cmd.extend(["--model", model])

        env = os.environ.copy()
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        else:
            oauth_token = get_oauth_token()
            if oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                self._process.communicate(), timeout=300
            )
            self._process = None

            if stdout:
                try:
                    data = json.loads(stdout.decode("utf-8", errors="replace"))
                    return data.get("result", data.get("text", stdout.decode()[:500]))
                except json.JSONDecodeError:
                    return stdout.decode("utf-8", errors="replace")[:500]

            if stderr:
                return f"[Error] {stderr.decode('utf-8', errors='replace')[:200]}"

            return "[No response]"

        except asyncio.TimeoutError:
            if self._process:
                self._process.kill()
                self._process = None
            return "[Timeout - message processing took too long]"
        except Exception as e:
            return f"[Error] {str(e)[:200]}"

    async def _send_reply(self, to_agent_id: str, message: str) -> bool:
        """Send reply back to the sender agent via Redis queue + persist event."""
        try:
            if not self.redis:
                return False
            payload = {
                "id": f"reply-{to_agent_id}-{self.agent_id}",
                "from_agent_id": self.agent_id,
                "from_name": settings.agent_name,
                "text": message,
                "to_agent_id": to_agent_id,
                "is_reply": True,
            }
            # Push to recipient's message queue
            await self.redis.lpush(f"agent:{to_agent_id}:messages", json.dumps(payload))
            # Publish for DB persistence (orchestrator listens on this channel)
            await self.redis.publish("agent:messages:persist", json.dumps(payload))
            return True
        except Exception as e:
            logger.warning(f"Failed to send reply to {to_agent_id}: {e}")
            return False

    async def start(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        log_publisher = LogPublisher(self.redis, self.agent_id)

        while self.running:
            try:
                # BRPOP blocks until a message is available (timeout 5s)
                result = await self.redis.brpop(self.queue_name, timeout=5)
                if result is None:
                    continue

                _, msg_json = result
                msg = json.loads(msg_json)

                from_agent_id = msg.get("from_agent_id", "unknown")
                from_name = msg.get("from_name", "Unknown Agent")
                text = msg.get("text", "")
                message_id = msg.get("id", "msg-unknown")
                is_reply = msg.get("is_reply", False)

                logger.info(f"[Message] {'Reply' if is_reply else 'New'} from {from_name} ({from_agent_id}): {text[:80]}...")

                # Rate limit check — reset counts every hour
                import time
                now = time.time()
                if now > self._reply_reset_at:
                    self._reply_counts = {}
                    self._reply_reset_at = now + 3600

                pair_count = self._reply_counts.get(from_agent_id, 0)
                if pair_count >= self.MAX_REPLIES_PER_PAIR:
                    logger.info(f"[Message] Rate limited: already replied {pair_count}x to {from_name} this hour")
                    continue
                self._reply_counts[from_agent_id] = pair_count + 1

                # Publish status
                await log_publisher.publish_status("working", f"msg:{message_id}")
                await log_publisher.publish(message_id, "system", {
                    "message": f"Processing message from {from_name}..."
                })

                # Load conversation history for context
                history_text = await self._get_conversation_history(from_agent_id)

                # Approval rules (user-defined triggers for request_approval)
                from app.runner_hooks import get_approval_rules_prefix
                rules_prefix = get_approval_rules_prefix()

                # Knowledge/Memory context prefix (MUST be included in every message)
                context_prefix = rules_prefix + (
                    f"MANDATORY FIRST STEPS — do these BEFORE processing the message:\n"
                    f"1. Read /workspace/knowledge.md to recall your role, skills, and learned patterns\n"
                    f"2. Use knowledge_search (query relevant to this message) to check shared knowledge\n"
                    f"3. Use memory_search (query: '') for recent memories and preferences\n\n"
                )

                # Build prompt with full conversation context
                if is_reply:
                    prompt = (
                        f"{context_prefix}"
                        f"Du bist Agent '{settings.agent_name}'. Agent '{from_name}' hat auf deine Anfrage geantwortet.\n\n"
                        f"=== KONVERSATIONSVERLAUF ===\n{history_text}\n"
                        f"=== ANTWORT von {from_name} ===\n{text}\n\n"
                        f"REGELN:\n"
                        f"- Verarbeite die erhaltenen Informationen und arbeite damit weiter.\n"
                        f"- Du KANNST Bash, Read, Write, Glob, Grep nutzen — arbeite wirklich!\n"
                        f"- Speichere Ergebnisse in /workspace/transfer/<thema>.md\n"
                        f"- NICHT antworten — dein Output wird NICHT zurückgesendet.\n"
                        f"- Wenn du weitere Infos von einem Agent brauchst: nutze send_message.\n"
                        f"- Wenn alles erledigt: nur 'Erledigt.' ausgeben.\n"
                        f"- NACH ABSCHLUSS: Speichere was du gelernt hast mit memory_save (category: 'learning')."
                    )
                else:
                    prompt = (
                        f"{context_prefix}"
                        f"Inter-agent Auftrag von '{from_name}':\n{text}\n\n"
                        f"=== KONTEXT (bisherige Nachrichten) ===\n{history_text}\n\n"
                        f"REGELN:\n"
                        f"- Du KANNST Bash, Read, Write, Glob, Grep nutzen — arbeite wirklich!\n"
                        f"- Speichere Ergebnisse in /workspace/transfer/<thema>.md\n"
                        f"- Dein Output wird automatisch an {from_name} zurückgesendet.\n"
                        f"- Halte die Antwort kurz — verweise auf Dateien statt langer Texte.\n"
                        f"- Wenn du nichts beitragen kannst: 'Keine Informationen vorhanden.'\n"
                        f"- NACH ABSCHLUSS: Speichere was du gelernt hast mit memory_save (category: 'learning')."
                    )

                # Execute via CLI
                response = await self._execute_cli(prompt)

                if response and not response.startswith("[Error]") and not response.startswith("[Timeout"):
                    if is_reply:
                        # Reply processed — no response back (agent worked with the info)
                        logger.info(f"[Message] Processed reply from {from_name}: {response[:80]}...")
                        await log_publisher.publish(message_id, "system", {
                            "message": f"Reply von {from_name} verarbeitet"
                        })
                    else:
                        # New request — send response back
                        sent = await self._send_reply(from_agent_id, response)
                        if sent:
                            logger.info(f"[Message] Replied to {from_name}: {response[:80]}...")
                            await log_publisher.publish(message_id, "system", {
                                "message": f"Replied to {from_name}"
                            })
                        else:
                            logger.warning(f"[Message] Failed to send reply to {from_name}")
                else:
                    logger.warning(f"[Message] CLI error for message from {from_name}: {response[:100] if response else 'empty'}")

                await log_publisher.publish_status("idle")

            except aioredis.ConnectionError:
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"[Message] Consumer error: {e}")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self.running = False
        if self._process:
            try:
                self._process.kill()
            except Exception:
                pass
        if self.redis:
            await self.redis.aclose()
