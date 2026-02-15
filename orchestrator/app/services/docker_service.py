import docker
from docker.errors import NotFound, APIError


class DockerService:
    """Wraps Docker SDK for container management."""

    def __init__(self):
        self.client = docker.from_env()

    def create_container(
        self,
        image: str,
        name: str,
        environment: dict,
        volume_name: str,
        network: str,
        memory_limit: str = "2g",
        cpu_quota: int = 100000,
        session_volume_name: str | None = None,
        shared_volume_name: str | None = None,
    ) -> docker.models.containers.Container:
        # Ensure volumes exist
        for vol in [volume_name, session_volume_name, shared_volume_name]:
            if vol:
                try:
                    self.client.volumes.get(vol)
                except NotFound:
                    self.client.volumes.create(name=vol)

        volumes = {volume_name: {"bind": "/workspace", "mode": "rw"}}
        if session_volume_name:
            volumes[session_volume_name] = {"bind": "/home/agent/.claude", "mode": "rw"}
        if shared_volume_name:
            volumes[shared_volume_name] = {"bind": "/shared", "mode": "rw"}

        container = self.client.containers.run(
            image=image,
            name=name,
            detach=True,
            environment=environment,
            volumes=volumes,
            network=network,
            mem_limit=memory_limit,
            cpu_quota=cpu_quota,
            restart_policy={"Name": "unless-stopped"},
            labels={"ai-employee.type": "agent"},
        )
        return container

    def get_container(self, container_id: str):
        return self.client.containers.get(container_id)

    def stop_container(self, container_id: str) -> None:
        container = self.client.containers.get(container_id)
        container.stop(timeout=30)

    def start_container(self, container_id: str) -> None:
        container = self.client.containers.get(container_id)
        container.start()

    def remove_container(self, container_id: str, force: bool = True) -> None:
        container = self.client.containers.get(container_id)
        container.remove(force=force)

    def remove_volume(self, volume_name: str) -> None:
        try:
            volume = self.client.volumes.get(volume_name)
            volume.remove(force=True)
        except NotFound:
            pass

    def get_container_stats(self, container_id: str) -> dict:
        container = self.client.containers.get(container_id)
        stats = container.stats(stream=False)

        cpu_percent = 0.0
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        if system_delta > 0 and cpu_delta > 0:
            num_cpus = len(
                stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
            )
            cpu_percent = (cpu_delta / system_delta) * num_cpus * 100

        mem_usage = stats.get("memory_stats", {}).get("usage", 0)
        mem_limit = stats.get("memory_stats", {}).get("limit", 1)

        return {
            "cpu_percent": round(cpu_percent, 2),
            "memory_usage_mb": round(mem_usage / (1024 * 1024), 2),
            "memory_limit_mb": round(mem_limit / (1024 * 1024), 2),
            "memory_percent": round((mem_usage / mem_limit) * 100, 2) if mem_limit > 0 else 0,
        }

    def get_image_id(self, image_name: str) -> str | None:
        """Get the current image ID for a given image name."""
        try:
            image = self.client.images.get(image_name)
            return image.id
        except Exception:
            return None

    def get_container_image_id(self, container_id: str) -> str | None:
        """Get the image ID that a container was built from."""
        try:
            container = self.client.containers.get(container_id)
            return container.image.id
        except Exception:
            return None

    def get_container_status(self, container_id: str) -> str:
        try:
            container = self.client.containers.get(container_id)
            return container.status
        except (NotFound, APIError):
            return "unknown"

    def list_agent_containers(self) -> list:
        containers = self.client.containers.list(
            all=True, filters={"label": "ai-employee.type=agent"}
        )
        return [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "labels": c.labels,
            }
            for c in containers
        ]

    def exec_in_container(self, container_id: str, cmd: str) -> tuple[int, str]:
        container = self.client.containers.get(container_id)
        exit_code, output = container.exec_run(cmd, demux=True)
        stdout = output[0].decode("utf-8", errors="replace") if output[0] else ""
        return exit_code, stdout

    def write_file_in_container(self, container_id: str, path: str, content: str) -> None:
        """Write a file into a running container using tar archive."""
        import io
        import tarfile

        container = self.client.containers.get(container_id)

        # Create a tar archive with the file
        data = content.encode("utf-8")
        filename = path.split("/")[-1]
        dir_path = "/".join(path.split("/")[:-1]) or "/"

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tar_stream.seek(0)

        container.put_archive(dir_path, tar_stream)

    def write_files_in_container(
        self, container_id: str, target_dir: str, files: list[tuple[str, bytes]]
    ) -> None:
        """Write multiple files into a container directory using a single tar archive."""
        import io
        import tarfile

        container = self.client.containers.get(container_id)

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for filename, data in files:
                info = tarfile.TarInfo(name=filename)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        tar_stream.seek(0)

        container.put_archive(target_dir, tar_stream)

    def get_file_from_container(self, container_id: str, path: str) -> bytes:
        import io
        import tarfile

        container = self.client.containers.get(container_id)
        bits, _ = container.get_archive(path)
        stream = io.BytesIO(b"".join(bits))
        with tarfile.open(fileobj=stream) as tar:
            member = tar.getmembers()[0]
            f = tar.extractfile(member)
            if f is None:
                raise FileNotFoundError(f"Cannot read {path}")
            return f.read()
