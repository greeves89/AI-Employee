import logging
import os
import shlex

from app.services.docker_service import DockerService

logger = logging.getLogger(__name__)

# Upload limits
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB per file
MAX_UPLOAD_TOTAL_BYTES = 200 * 1024 * 1024  # 200 MB total per upload batch

# Blocked file extensions (dangerous executables / scripts that could escape container)
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".pif",
    ".msi", ".dll", ".sys", ".drv",
}


def _validate_path(path: str) -> str:
    """Validate and sanitize a container file path."""
    # Null byte injection check
    if "\x00" in path:
        raise ValueError("Null bytes not allowed in path")

    # Block path traversal (normalized check)
    normalized = os.path.normpath(path)
    if ".." in normalized.split("/"):
        raise ValueError("Path traversal not allowed")

    # Must be absolute
    if not path.startswith("/"):
        raise ValueError("Path must be absolute")

    # Block overly long paths
    if len(path) > 4096:
        raise ValueError("Path too long")

    return shlex.quote(path)


def _validate_filename(filename: str) -> str:
    """Validate an uploaded filename."""
    if not filename or not filename.strip():
        raise ValueError("Empty filename")

    # Null byte check
    if "\x00" in filename:
        raise ValueError("Null bytes not allowed in filename")

    # Path traversal in filename
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError("Invalid characters in filename")

    # Blocked extensions
    _, ext = os.path.splitext(filename.lower())
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError(f"File extension '{ext}' is not allowed")

    # Overly long filenames
    if len(filename) > 255:
        raise ValueError("Filename too long")

    return filename


class FileManager:
    """Manages file access in agent workspace volumes via Docker exec."""

    def __init__(self, docker: DockerService):
        self.docker = docker

    def list_directory(self, container_id: str, path: str = "/workspace") -> list[dict]:
        safe_path = _validate_path(path)
        # Use -not -type l to exclude symlinks from listing for security
        cmd = (
            f'find {safe_path} -maxdepth 1 -not -path {safe_path} -not -type l '
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
        _validate_path(file_path)
        # Verify it's not a symlink before reading
        safe_path = shlex.quote(file_path)
        exit_code, output = self.docker.exec_in_container(
            container_id, f"bash -c 'test -L {safe_path} && echo SYMLINK || echo OK'"
        )
        if output.strip() == "SYMLINK":
            raise ValueError("Cannot read symlinks for security reasons")

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

        # Validate each file
        total_size = 0
        for filename, content in files:
            _validate_filename(filename)

            if len(content) > MAX_FILE_SIZE_BYTES:
                raise ValueError(
                    f"File '{filename}' exceeds maximum size "
                    f"({len(content)} > {MAX_FILE_SIZE_BYTES} bytes)"
                )
            total_size += len(content)

        if total_size > MAX_UPLOAD_TOTAL_BYTES:
            raise ValueError(
                f"Total upload size exceeds limit "
                f"({total_size} > {MAX_UPLOAD_TOTAL_BYTES} bytes)"
            )

        safe_path = _validate_path(target_path)
        self.docker.exec_in_container(
            container_id, f"mkdir -p {safe_path}"
        )

        self.docker.write_files_in_container(container_id, target_path, files)
        logger.info(f"Uploaded {len(files)} files ({total_size} bytes) to {target_path}")
        return len(files)

    def get_file_info(self, container_id: str, file_path: str) -> dict:
        safe_path = _validate_path(file_path)
        cmd = f'stat -c "%s|%Y|%F" {safe_path} 2>/dev/null'
        exit_code, output = self.docker.exec_in_container(container_id, f"bash -c '{cmd}'")

        if exit_code != 0:
            raise FileNotFoundError(f"File not found: {file_path}")

        parts = output.strip().split("|")
        return {
            "size": int(parts[0]) if len(parts) > 0 else 0,
            "modified": int(parts[1]) if len(parts) > 1 else 0,
            "type": parts[2] if len(parts) > 2 else "unknown",
        }
