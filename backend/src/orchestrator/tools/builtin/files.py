"""Workspace-scoped file tools.

Paths are untrusted model output. Every path is resolved to canonical form and
must remain inside the workspace root — `..`, absolute paths, and symlink
escapes are rejected before any filesystem operation.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from orchestrator.tools.base import Tool

_MAX_READ_CHARS = 100_000
_MAX_WRITE_CHARS = 500_000


def _resolve_inside(root: Path, user_path: str) -> Path:
    """Canonicalize and confine. Raises ValueError on escape attempts."""
    candidate = (root / user_path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError(f"path {user_path!r} escapes the workspace")
    return candidate


class ReadFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace root, e.g. 'notes/draft.md'.")


class WriteFileArgs(BaseModel):
    path: str = Field(description="Path relative to the workspace root, e.g. 'notes/draft.md'.")
    content: str = Field(
        max_length=_MAX_WRITE_CHARS,
        description="Full file content to write. Overwrites if the file exists.",
    )


class FileReadTool(Tool[ReadFileArgs]):
    name = "read_file"
    description = "Read a text file from the shared task workspace."
    args_model = ReadFileArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def execute(self, args: ReadFileArgs) -> str:
        target = _resolve_inside(self._root, args.path)
        if not target.is_file():
            raise ValueError(f"no such file: {args.path!r}")
        text = target.read_text(encoding="utf-8")
        if len(text) > _MAX_READ_CHARS:
            return text[:_MAX_READ_CHARS] + f"\n... [truncated at {_MAX_READ_CHARS} chars]"
        return text


class FileWriteTool(Tool[WriteFileArgs]):
    name = "write_file"
    description = "Write a text file into the shared task workspace (overwrites)."
    args_model = WriteFileArgs

    def __init__(self, root: Path) -> None:
        self._root = root

    async def execute(self, args: WriteFileArgs) -> str:
        target = _resolve_inside(self._root, args.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args.content, encoding="utf-8")
        return f"wrote {len(args.content)} chars to {args.path}"
