from fastapi import APIRouter

from app.api import agents, schedules, tasks, ws, settings

api_router = APIRouter()
api_router.include_router(agents.router)
api_router.include_router(tasks.router)
api_router.include_router(schedules.router)
api_router.include_router(ws.router)
api_router.include_router(settings.router)
