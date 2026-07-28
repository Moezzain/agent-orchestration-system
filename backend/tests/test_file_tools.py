from pathlib import Path

import pytest

from orchestrator.tools.builtin.files import (
    FileReadTool,
    FileWriteTool,
    ReadFileArgs,
    WriteFileArgs,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "ws"


async def test_write_then_read_roundtrip(workspace: Path) -> None:
    write = FileWriteTool(workspace)
    read = FileReadTool(workspace)

    confirmation = await write.execute(
        WriteFileArgs(path="notes/draft.md", content="# Findings\nhello")
    )
    content = await read.execute(ReadFileArgs(path="notes/draft.md"))

    assert "notes/draft.md" in confirmation
    assert content == "# Findings\nhello"


async def test_parent_directories_created_on_write(workspace: Path) -> None:
    write = FileWriteTool(workspace)

    await write.execute(WriteFileArgs(path="a/b/c/deep.txt", content="x"))

    assert (workspace / "a/b/c/deep.txt").exists()


class TestTraversalGuard:
    async def test_dotdot_escape_rejected(self, workspace: Path) -> None:
        read = FileReadTool(workspace)

        with pytest.raises(ValueError, match="escapes the workspace"):
            await read.execute(ReadFileArgs(path="../../etc/passwd"))

    async def test_absolute_path_outside_rejected(self, workspace: Path) -> None:
        write = FileWriteTool(workspace)

        with pytest.raises(ValueError, match="escapes the workspace"):
            await write.execute(WriteFileArgs(path="/etc/evil", content="x"))

    async def test_symlink_escape_rejected(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        workspace.mkdir(parents=True)
        (workspace / "link").symlink_to(outside)

        read = FileReadTool(workspace)

        with pytest.raises(ValueError, match="escapes the workspace"):
            await read.execute(ReadFileArgs(path="link/secret.txt"))


async def test_missing_file_raises_clear_error(workspace: Path) -> None:
    read = FileReadTool(workspace)
    workspace.mkdir(parents=True)

    with pytest.raises(ValueError, match="no such file"):
        await read.execute(ReadFileArgs(path="ghost.md"))


async def test_oversized_read_truncates(workspace: Path) -> None:
    write = FileWriteTool(workspace)
    read = FileReadTool(workspace)
    await write.execute(WriteFileArgs(path="big.txt", content="x" * 150_000))

    content = await read.execute(ReadFileArgs(path="big.txt"))

    assert len(content) < 150_000
    assert "truncated" in content
