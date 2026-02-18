from fastapi import APIRouter

from app.api import admin, agents, auth, feedback, integrations, memory, mcp_servers, notifications, schedules, tasks, templates, todos, webhooks, ws, settings

api_router = APIRouter()
api_router.include_router(admin.router)
api_router.include_router(auth.router)
api_router.include_router(agents.router)
api_router.include_router(feedback.router)
api_router.include_router(integrations.router)
api_router.include_router(memory.router)
api_router.include_router(mcp_servers.router)
api_router.include_router(notifications.router)
api_router.include_router(tasks.router)
api_router.include_router(templates.router)
api_router.include_router(todos.router)
api_router.include_router(schedules.router)
api_router.include_router(webhooks.router)
api_router.include_router(ws.router)
api_router.include_router(settings.router)
