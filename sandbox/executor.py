"""Sandbox executor – runs code inside ephemeral Docker containers.

Lifecycle:
  1. Create a container with the repo mounted at /workspace
  2. Detect dependency files (requirements.txt, package.json, pyproject.toml)
  3. Install dependencies inside the container
  4. Run the requested test command
  5. Capture stdout, stderr, exit_code
  6. Destroy the container (always, even on failure)

Requires:  docker (pip install docker)  +  Docker daemon running.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import (
    APIError,
    ContainerError,
    ImageNotFound,
    NotFound,
)
from docker.models.containers import Container

logger = logging.getLogger(__name__)


# ── Result dataclass ─────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    """Structured output from a sandbox run."""

    exit_code: int
    stdout: str
    stderr: str
    logs: str = ""               # combined stdout+stderr stream
    timed_out: bool = False
    duration_s: float = 0.0
    dependency_install: str = ""  # stdout of the install step
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "logs": self.logs,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 2),
            "dependency_install": self.dependency_install,
            "errors": self.errors,
            "success": self.success,
        }


# ── Dependency detection helpers ─────────────────────────────────────

DEP_INSTALL_COMMANDS: list[tuple[str, str]] = [
    # (file to look for inside /workspace, install command)
    ("requirements.txt", "pip install --no-cache-dir -r requirements.txt"),
    ("pyproject.toml",   "pip install --no-cache-dir ."),
    ("setup.py",         "pip install --no-cache-dir ."),
    ("package.json",     "npm install --production=false"),
    ("Pipfile",          "pip install pipenv && pipenv install --dev --system"),
]


def _build_install_script(dep_files: list[str]) -> str | None:
    """Return a shell snippet that installs every detected dependency set."""
    parts: list[str] = []
    has_python = False
    
    for sentinel, cmd in DEP_INSTALL_COMMANDS:
        if sentinel in dep_files:
            parts.append(f'echo ">>> Installing from {sentinel}" && {cmd}')
            if "pip" in cmd:
                has_python = True

    # Check if there are python files to ensure we have pytest
    has_py_files = any(f.endswith(".py") for f in dep_files)
    
    if has_python or has_py_files:
        parts.append('echo ">>> Installing pytest" && pip install --no-cache-dir pytest')

    if not parts:
        return None
    return " && ".join(parts)


# ── Core executor ────────────────────────────────────────────────────

class SandboxExecutor:
    """Creates ephemeral Docker containers to run untrusted code safely.

    Usage::

        executor = SandboxExecutor()
        result = await executor.run_tests(
            repo_path="/tmp/heal_abc123",
            test_command="python -m pytest --tb=short -q",
        )
        print(result.logs)
    """

    def __init__(
        self,
        image: str | None = None,
        timeout: int | None = None,
        memory_limit: str | None = None,
        cpu_limit: float | None = None,
        network_disabled: bool = True,
    ):
        self.image = image or os.getenv("SANDBOX_IMAGE", "python:3.11-slim")
        self.timeout = timeout or int(os.getenv("SANDBOX_TIMEOUT", "300"))
        self.memory_limit = memory_limit or os.getenv("SANDBOX_MEMORY_LIMIT", "512m")
        self.cpu_limit = cpu_limit or float(os.getenv("SANDBOX_CPU_LIMIT", "1.0"))
        self.network_disabled = network_disabled

        self._client: docker.DockerClient | None = None

    # -- Docker client (lazy) ------------------------------------------

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    # -- Public API ----------------------------------------------------

    async def run_tests(
        self,
        repo_path: str,
        test_command: str,
        install_deps: bool = True,
    ) -> ExecutionResult:
        """Mount *repo_path*, install deps, run *test_command*, return results.

        The container is **always** destroyed after execution.
        """
        import asyncio

        return await asyncio.to_thread(
            self._run_tests_sync, repo_path, test_command, install_deps
        )

    def _run_tests_sync(
        self,
        repo_path: str,
        test_command: str,
        install_deps: bool,
    ) -> ExecutionResult:
        container: Container | None = None
        t0 = time.monotonic()

        try:
            # ── 1. Pull image if missing ─────────────────────────────
            self._ensure_image()

            # ── 2. Create container (do NOT start yet) ───────────────
            container = self.client.containers.create(
                image=self.image,
                command="sleep infinity",      # keep alive for exec
                working_dir="/workspace",
                volumes={
                    os.path.abspath(repo_path): {
                        "bind": "/workspace",
                        "mode": "rw",
                    }
                },
                mem_limit=self.memory_limit,
                nano_cpus=int(self.cpu_limit * 1e9),
                network_disabled=self.network_disabled if not install_deps else False,
                labels={"managed-by": "self-healing-sandbox"},
                detach=True,
            )
            container.start()
            logger.info("Container %s started (image=%s)", container.short_id, self.image)

            # ── 3. Detect & install dependencies ─────────────────────
            dep_install_log = ""
            if install_deps:
                dep_install_log = self._install_dependencies(container)

            # Disable network after install for test safety
            if install_deps and self.network_disabled:
                self._disconnect_network(container)

            # ── 4. Run the test command ──────────────────────────────
            stdout, stderr, exit_code, timed_out = self._exec_in_container(
                container, test_command
            )

            duration = time.monotonic() - t0
            logs = self._fetch_logs(container)

            return ExecutionResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                logs=logs,
                timed_out=timed_out,
                duration_s=duration,
                dependency_install=dep_install_log,
            )

        except ImageNotFound:
            return self._error_result(t0, f"Docker image '{self.image}' not found")
        except APIError as exc:
            return self._error_result(t0, f"Docker API error: {exc.explanation}")
        except Exception as exc:
            return self._error_result(t0, str(exc))
        finally:
            # ── 5. ALWAYS destroy the container ──────────────────────
            self._destroy(container)

    # -- Execute a command inside a running container -------------------

    def _exec_in_container(
        self, container: Container, command: str
    ) -> tuple[str, str, int, bool]:
        """Run *command* via `docker exec` and return (stdout, stderr, exit_code, timed_out)."""
        timed_out = False
        try:
            exec_result = container.exec_run(
                cmd=["sh", "-c", command],
                workdir="/workspace",
                demux=True,                 # separate stdout / stderr
                environment={"PYTHONDONTWRITEBYTECODE": "1"},
            )

            raw_stdout = (exec_result.output[0] or b"").decode("utf-8", errors="replace") if exec_result.output else ""
            raw_stderr = (exec_result.output[1] or b"").decode("utf-8", errors="replace") if exec_result.output and len(exec_result.output) > 1 else ""
            exit_code = exec_result.exit_code

            return raw_stdout, raw_stderr, exit_code, False

        except Exception as exc:
            err_msg = str(exc).lower()
            timed_out = "timeout" in err_msg or "timed out" in err_msg
            return "", str(exc), 1, timed_out

    # -- Dependency installation ---------------------------------------

    def _install_dependencies(self, container: Container) -> str:
        """Detect dependency files in /workspace and install them."""
        # List files at repo root
        ls_result = container.exec_run(
            cmd=["ls", "/workspace"], demux=True
        )
        file_list = (ls_result.output[0] or b"").decode().split()

        script = _build_install_script(file_list)
        if script is None:
            logger.info("No dependency files detected – skipping install")
            return "(no dependency files detected)"

        logger.info("Installing dependencies: %s", script)
        stdout, stderr, code, _ = self._exec_in_container(container, script)
        combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"

        if code != 0:
            logger.warning("Dependency install exited with code %d", code)

        return combined

    # -- Network isolation after install -------------------------------

    @staticmethod
    def _disconnect_network(container: Container) -> None:
        """Best-effort disconnect from all networks for test isolation."""
        try:
            client = docker.from_env()
            for net_name in list(container.attrs.get("NetworkSettings", {}).get("Networks", {}).keys()):
                net = client.networks.get(net_name)
                net.disconnect(container)
                logger.debug("Disconnected container from %s", net_name)
        except Exception as exc:
            logger.debug("Could not disconnect network: %s", exc)

    # -- Logs ----------------------------------------------------------

    @staticmethod
    def _fetch_logs(container: Container) -> str:
        """Return combined container logs."""
        try:
            return container.logs(stdout=True, stderr=True).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return ""

    # -- Image ---------------------------------------------------------

    def _ensure_image(self) -> None:
        """Pull the sandbox image if it isn't available locally."""
        try:
            self.client.images.get(self.image)
        except ImageNotFound:
            logger.info("Pulling image %s …", self.image)
            self.client.images.pull(self.image)

    # -- Cleanup -------------------------------------------------------

    @staticmethod
    def _destroy(container: Container | None) -> None:
        """Force-remove the container.  Never raises."""
        if container is None:
            return
        try:
            container.remove(force=True)
            logger.info("Container %s destroyed", container.short_id)
        except NotFound:
            pass  # already gone
        except Exception as exc:
            logger.warning("Failed to destroy container: %s", exc)

    # -- Error helper --------------------------------------------------

    @staticmethod
    def _error_result(t0: float, message: str) -> ExecutionResult:
        return ExecutionResult(
            exit_code=1,
            stdout="",
            stderr=message,
            logs=message,
            duration_s=time.monotonic() - t0,
            errors=[message],
        )

