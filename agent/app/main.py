import asyncio
import signal

from app.config import settings
from app.health import start_health_server
from app.task_consumer import TaskConsumer
from app.chat_consumer import ChatConsumer


async def main() -> None:
    agent_id = settings.agent_id
    print(f"[Agent {agent_id}] Starting up...")

    # Start health server
    health_runner = await start_health_server(agent_id, settings.health_port)
    print(f"[Agent {agent_id}] Health server on port {settings.health_port}")

    # Start task consumer
    task_consumer = TaskConsumer(agent_id)

    # Start chat consumer
    chat_consumer = ChatConsumer(agent_id)

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(
                shutdown(task_consumer, chat_consumer, health_runner)
            ),
        )

    print(f"[Agent {agent_id}] Listening for tasks on queue agent:{agent_id}:tasks")
    print(f"[Agent {agent_id}] Listening for chat on queue agent:{agent_id}:chat")

    # Run both consumers concurrently
    await asyncio.gather(
        task_consumer.start(),
        chat_consumer.start(),
    )


async def shutdown(task_consumer: TaskConsumer, chat_consumer: ChatConsumer, health_runner) -> None:
    print("[Agent] Shutting down gracefully...")
    await task_consumer.stop()
    await chat_consumer.stop()
    await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
