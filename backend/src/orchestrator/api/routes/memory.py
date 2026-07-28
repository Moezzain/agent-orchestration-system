"""Memory endpoints: user-data deletion (the BRD's delete requirement)."""

from fastapi import APIRouter, HTTPException

from orchestrator.api.deps import SettingsDep
from orchestrator.memory import LongTermMemory, OpenAIEmbedder, create_chroma_client

router = APIRouter(prefix="/memory", tags=["memory"])


@router.delete("/task/{task_id}", status_code=204)
async def delete_task_memories(task_id: str, settings: SettingsDep) -> None:
    """Wipe every long-term memory a task contributed."""
    if settings.openai_api_key is None:
        raise HTTPException(status_code=503, detail="memory subsystem not configured")
    try:
        chroma = await create_chroma_client(settings)
        memory = LongTermMemory(chroma, OpenAIEmbedder(settings.openai_api_key.get_secret_value()))
        await memory.delete_for_task(task_id)
    except HTTPException:
        raise
    except Exception as exc:  # chroma unreachable etc.
        raise HTTPException(status_code=503, detail=f"memory store unavailable: {exc}") from exc
