"""Sandboxed Python execution in ephemeral Docker containers.

Threat model honesty: this contains untrusted LLM-generated code with Docker
isolation — no network, no filesystem, tight resource limits, non-root, and a
hard wall-clock kill. Container escape is out of scope; production would use
gVisor/Firecracker or a hosted sandbox (E2B, Modal). Say exactly that when
asked.

The code exchanges data with the rest of the system only through stdout —
no mounts, so there is nothing to traverse.
"""

import asyncio
from uuid import uuid4

from pydantic import BaseModel, Field

from orchestrator.tools.base import Tool

_IMAGE = "python:3.12-slim"
_OUTPUT_CAP = 20_000
_MAX_CODE_CHARS = 50_000


def build_docker_args(container_name: str, image: str = _IMAGE) -> list[str]:
    """The sandbox policy, as docker-run flags. Pure function so it's testable."""
    return [
        "docker",
        "run",
        "--rm",  # self-cleaning
        "-i",  # code arrives via stdin
        "--name",
        container_name,  # addressable for kill-on-timeout
        "--network=none",  # no exfiltration, no lateral movement
        "--memory=256m",
        "--cpus=1",
        "--pids-limit=128",  # fork-bomb ceiling
        "--read-only",  # immutable rootfs
        "--tmpfs",
        "/tmp:size=64m,noexec",  # scratch space, non-executable
        "--user",
        "65534:65534",  # nobody
        image,
        "python",
        "-",  # read program from stdin
    ]


class RunPythonArgs(BaseModel):
    code: str = Field(
        max_length=_MAX_CODE_CHARS,
        description=(
            "Python 3.12 code to run in an isolated sandbox. No network, no files, "
            "stdlib only. Print your results — stdout is the only channel back."
        ),
    )


class CodeExecutionTool(Tool[RunPythonArgs]):
    name = "run_python"
    description = (
        "Execute Python code in a sandbox and return its output. "
        "Use for calculations, data transforms, and algorithm checks."
    )
    args_model = RunPythonArgs

    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self._timeout = timeout_seconds

    async def execute(self, args: RunPythonArgs) -> str:
        container_name = f"sbx-{uuid4().hex}"
        try:
            process = await asyncio.create_subprocess_exec(
                *build_docker_args(container_name),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ValueError("docker is not available on this host") from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(args.code.encode()), timeout=self._timeout
            )
        except TimeoutError:
            # --rm removes the container once killed; fire-and-forget the kill
            killer = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            # reap the docker-run process (its container just died) so the
            # transport is closed on a live event loop
            process.kill()
            await process.wait()
            raise ValueError(
                f"execution exceeded {self._timeout}s and was killed — "
                "reduce the workload or optimize the code"
            ) from None

        stdout = stdout_b.decode(errors="replace")[:_OUTPUT_CAP]
        stderr = stderr_b.decode(errors="replace")[:_OUTPUT_CAP]

        if process.returncode != 0:
            # Traceback goes back to the LLM — it reads it and fixes the code
            raise ValueError(f"exit code {process.returncode}\n{stderr or stdout}")

        return stdout if not stderr else f"{stdout}\n[stderr]\n{stderr}"
