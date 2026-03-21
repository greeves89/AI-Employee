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

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:messages"
        self.running = True
        self._process: asyncio.subprocess.Process | None = None

    async def _execute_cli(self, prompt: str, model: str | None = None) -> str:
        """Run Claude CLI with the prompt and return the text response."""
        import os

        cmd = ["claude", "-p", prompt, "--output-format", "json"]
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
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                self._process.communicate(), timeout=120
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
        """Send reply back to the sender agent via orchestrator API."""
        import urllib.request
        import urllib.error

        url = f"{settings.orchestrator_url}/api/v1/agents/{to_agent_id}/message"
        payload = json.dumps({
            "from_agent_id": self.agent_id,
            "from_name": settings.agent_name,
            "text": message,
        }).encode()

        # Build agent token for auth
        import hashlib
        import hmac as hmac_mod
        token = hmac_mod.new(
            settings.agent_token.encode() if hasattr(settings, "agent_token") and settings.agent_token else b"",
            b"",
            hashlib.sha256,
        ).hexdigest()

        # Use the AGENT_TOKEN directly (it's already the HMAC token)
        agent_token = settings.agent_token

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {agent_token}",
                "X-Agent-ID": self.agent_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
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

                logger.info(f"[Message] From {from_name} ({from_agent_id}): {text[:80]}...")

                # Publish status
                await log_publisher.publish_status("working", f"msg:{message_id}")
                await log_publisher.publish(message_id, "system", {
                    "message": f"Processing message from {from_name}..."
                })

                # Build prompt with context
                prompt = (
                    f"You received a message from another agent named '{from_name}' "
                    f"(agent ID: {from_agent_id}).\n\n"
                    f"Their message:\n{text}\n\n"
                    f"Respond helpfully and concisely. Your response will be sent "
                    f"back to them automatically."
                )

                # Execute via CLI
                response = await self._execute_cli(prompt)

                # Send reply back
                if response and not response.startswith("[Error]") and not response.startswith("[Timeout"):
                    sent = await self._send_reply(from_agent_id, response)
                    if sent:
                        logger.info(f"[Message] Replied to {from_name}: {response[:80]}...")
                        await log_publisher.publish(message_id, "system", {
                            "message": f"Replied to {from_name}"
                        })
                    else:
                        logger.warning(f"[Message] Failed to send reply to {from_name}")
                        await log_publisher.publish(message_id, "system", {
                            "message": f"Reply failed to {from_name}: could not reach orchestrator"
                        })
                else:
                    logger.warning(f"[Message] CLI error for message from {from_name}: {response[:100]}")
                    await log_publisher.publish(message_id, "system", {
                        "message": f"Message processing failed: {response[:100]}"
                    })

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
