"""Sandbox integration tests — need a running docker daemon (skipped otherwise).

First run may be slow (pulls python:3.12-slim).
"""

import asyncio
import shutil

import pytest

from orchestrator.tools.builtin.code_exec import CodeExecutionTool, RunPythonArgs


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    import subprocess

    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except subprocess.TimeoutExpired:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="docker not available")


async def test_runs_code_and_returns_stdout() -> None:
    tool = CodeExecutionTool()

    output = await tool.execute(RunPythonArgs(code="print(sum(range(101)))"))

    assert output.strip() == "5050"


async def test_traceback_returned_for_llm_to_fix() -> None:
    tool = CodeExecutionTool()

    with pytest.raises(ValueError, match="ZeroDivisionError"):
        await tool.execute(RunPythonArgs(code="print(1/0)"))


async def test_network_is_unreachable() -> None:
    tool = CodeExecutionTool()
    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('https://example.com', timeout=3)\n"
        "    print('NETWORK OK')\n"
        "except Exception as e:\n"
        "    print(f'BLOCKED: {type(e).__name__}')\n"
    )

    output = await tool.execute(RunPythonArgs(code=code))

    assert "BLOCKED" in output
    assert "NETWORK OK" not in output


async def test_infinite_loop_killed_at_timeout() -> None:
    tool = CodeExecutionTool(timeout_seconds=3.0)

    with pytest.raises(ValueError, match="exceeded 3.0s"):
        await tool.execute(RunPythonArgs(code="while True: pass"))

    # container actually gone (— docker kill + --rm), not orphaned
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "ps",
        "--filter",
        "name=sbx-",
        "--format",
        "{{.Names}}",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    assert stdout.decode().strip() == ""


async def test_filesystem_is_read_only() -> None:
    tool = CodeExecutionTool()
    code = (
        "try:\n"
        "    open('/evil.txt', 'w')\n"
        "    print('WROTE')\n"
        "except OSError as e:\n"
        "    print(f'DENIED: {e.__class__.__name__}')\n"
    )

    output = await tool.execute(RunPythonArgs(code=code))

    assert "DENIED" in output
