from fastapi import APIRouter

from app.api import agents, integrations, memory, notifications, schedules, tasks, webhooks, ws, settings

api_router = APIRouter()
api_router.include_router(agents.router)
api_router.include_router(integrations.router)
api_router.include_router(memory.router)
api_router.include_router(notifications.router)
api_router.include_router(tasks.router)
api_router.include_router(schedules.router)
api_router.include_router(webhooks.router)
api_router.include_router(ws.router)
api_router.include_router(settings.router)
