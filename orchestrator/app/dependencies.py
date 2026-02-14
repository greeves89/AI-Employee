from fastapi import Request

from app.services.docker_service import DockerService
from app.services.redis_service import RedisService


def get_redis_service(request: Request) -> RedisService:
    return request.app.state.redis


def get_docker_service(request: Request) -> DockerService:
    return request.app.state.docker
