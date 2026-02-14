from app.services.docker_service import DockerService


class FileManager:
    """Manages file access in agent workspace volumes via Docker exec."""

    def __init__(self, docker: DockerService):
        self.docker = docker

    def list_directory(self, container_id: str, path: str = "/workspace") -> list[dict]:
        # Use find to get file info: type, size, modified time, name
        cmd = (
            f'find {path} -maxdepth 1 -not -path {path} '
            f'-printf "%y|%s|%T@|%f\\n" 2>/dev/null | sort'
        )
        exit_code, output = self.docker.exec_in_container(container_id, f"bash -c '{cmd}'")

        entries = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) != 4:
                continue

            file_type, size, mtime, name = parts
            entries.append(
                {
                    "name": name,
                    "type": "directory" if file_type == "d" else "file",
                    "size": int(size) if size.isdigit() else 0,
                    "modified": float(mtime) if mtime else 0,
                    "path": f"{path.rstrip('/')}/{name}",
                }
            )

        return entries

    def read_file(self, container_id: str, file_path: str) -> bytes:
        return self.docker.get_file_from_container(container_id, file_path)

    async def upload_files(
        self, container_id: str, target_path: str, files: list[tuple[str, bytes]]
    ) -> int:
        """Upload multiple files to a container directory.

        Args:
            container_id: Docker container ID
            target_path: Target directory in container (must be under /workspace)
            files: List of (filename, content_bytes) tuples

        Returns:
            Number of files uploaded
        """
        # Security: ensure target is under /workspace
        if not target_path.startswith("/workspace"):
            raise ValueError("Upload target must be under /workspace")

        # Ensure target directory exists
        self.docker.exec_in_container(
            container_id, f"mkdir -p {target_path}"
        )

        self.docker.write_files_in_container(container_id, target_path, files)
        return len(files)

    def get_file_info(self, container_id: str, file_path: str) -> dict:
        cmd = f'stat -c "%s|%Y|%F" {file_path} 2>/dev/null'
        exit_code, output = self.docker.exec_in_container(container_id, f"bash -c '{cmd}'")

        if exit_code != 0:
            raise FileNotFoundError(f"File not found: {file_path}")

        parts = output.strip().split("|")
        return {
            "size": int(parts[0]) if len(parts) > 0 else 0,
            "modified": int(parts[1]) if len(parts) > 1 else 0,
            "type": parts[2] if len(parts) > 2 else "unknown",
        }
