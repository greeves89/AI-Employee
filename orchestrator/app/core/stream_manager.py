import asyncio
import json

from fastapi import WebSocket
from app.services.redis_service import RedisService


class StreamManager:
    """Bridges Redis PubSub to WebSocket connections for real-time log streaming."""

    def __init__(self, redis: RedisService):
        self.redis = redis
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        await websocket.accept()
        self.active_connections.setdefault(channel, []).append(websocket)

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        if channel in self.active_connections:
            self.active_connections[channel] = [
                ws for ws in self.active_connections[channel] if ws != websocket
            ]
            if not self.active_connections[channel]:
                del self.active_connections[channel]

    async def _send_history(self, websocket: WebSocket, agent_id: str) -> None:
        """Send recent activity history to a newly connected client."""
        if not self.redis.client:
            return
        history_key = f"agent:{agent_id}:activity"
        events = await self.redis.client.lrange(history_key, -50, -1)  # Last 50
        if not events:
            return
        # Signal frontend to clear existing messages before receiving history
        await websocket.send_text(json.dumps({"type": "history_start"}))
        for event in events:
            if isinstance(event, bytes):
                event = event.decode("utf-8")
            await websocket.send_text(event)

    async def stream_agent_logs(self, websocket: WebSocket, agent_id: str) -> None:
        channel = f"agent:{agent_id}:logs"
        await self.connect(websocket, channel)

        # Send recent history before subscribing to live events
        await self._send_history(websocket, agent_id)

        pubsub = await self.redis.subscribe(channel)

        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await websocket.send_text(data)
                await asyncio.sleep(0.01)
        except Exception:
            pass
        finally:
            self.disconnect(websocket, channel)
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def stream_all_logs(self, websocket: WebSocket) -> None:
        channel = "agents:logs:all"
        await self.connect(websocket, channel)

        pubsub = await self.redis.subscribe(channel)

        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await websocket.send_text(data)
                await asyncio.sleep(0.01)
        except Exception:
            pass
        finally:
            self.disconnect(websocket, channel)
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
