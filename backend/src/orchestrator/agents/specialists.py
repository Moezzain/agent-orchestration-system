"""Specialists = AgentRuntime configurations. No new behavior, only config:
which prompt, which tools, which turn budget. The runtime does the rest.

Prompts state the job, the tool discipline, and the workspace convention
(files are how specialists hand artifacts to each other).
"""

from pydantic import BaseModel, ConfigDict

from orchestrator.agents.runtime import AgentRuntime, ResponseCallback
from orchestrator.llm.router import ModelRouter
from orchestrator.schemas import AgentRole, SpecialistType
from orchestrator.tools.registry import ToolRegistry


class SpecialistConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    specialist: SpecialistType
    system_prompt: str
    tool_names: list[str]
    max_turns: int = 8


_RESEARCH = SpecialistConfig(
    specialist=SpecialistType.RESEARCH,
    system_prompt="""You are a research specialist in a multi-agent team.
Your job: gather accurate, current information for the subtask you are given.

Discipline:
- Use web_search for anything you are not certain about; prefer multiple sources.
- Cite sources inline as [n] with their URLs listed at the end.
- Save substantial findings to the shared workspace with write_file (e.g.
  'research/<topic>.md') so teammates can build on them; mention saved paths
  in your answer.
- Report facts and disagreements between sources honestly; do not smooth over
  uncertainty.""",
    tool_names=["web_search", "write_file", "read_file"],
)

_WRITER = SpecialistConfig(
    specialist=SpecialistType.WRITER,
    system_prompt="""You are a writing specialist in a multi-agent team.
Your job: turn research and analysis into the requested deliverable.

Discipline:
- Read teammate outputs from the shared workspace with read_file when the
  subtask context names files.
- Match the expected output format exactly (markdown, report, summary, table).
- Preserve source citations from research you use.
- Write the final deliverable to the workspace with write_file and include its
  content in your answer.""",
    tool_names=["read_file", "write_file"],
    max_turns=6,
)

_DATA_ANALYSIS = SpecialistConfig(
    specialist=SpecialistType.DATA_ANALYSIS,
    system_prompt="""You are a data-analysis specialist in a multi-agent team.
Your job: compute, don't guess. Any number you report must come from running
code or from provided data.

Discipline:
- Use run_python for all calculations; print intermediate values you rely on.
- The sandbox has no network and no files — inline your data in the code.
  Read inputs from the workspace with read_file first if a file is named.
- State assumptions explicitly; show the numbers behind every conclusion.""",
    tool_names=["run_python", "read_file", "write_file"],
)

_CODE_EXECUTION = SpecialistConfig(
    specialist=SpecialistType.CODE_EXECUTION,
    system_prompt="""You are a code-execution specialist in a multi-agent team.
Your job: write and run Python to solve the subtask, iterating on errors.

Discipline:
- Run your code with run_python; never claim untested code works.
- On a traceback, fix and re-run — that loop is your core skill.
- The sandbox is stdlib-only, no network, no filesystem; print results.
- Deliver: what you ran, what it output, and what it means for the subtask.""",
    tool_names=["run_python", "read_file", "write_file"],
    max_turns=10,  # error-fix-rerun cycles need headroom
)

SPECIALIST_CONFIGS: dict[SpecialistType, SpecialistConfig] = {
    c.specialist: c for c in (_RESEARCH, _WRITER, _DATA_ANALYSIS, _CODE_EXECUTION)
}


def build_specialist(
    specialist: SpecialistType,
    *,
    router: ModelRouter,
    registry: ToolRegistry,
    on_llm_response: ResponseCallback | None = None,
) -> AgentRuntime:
    config = SPECIALIST_CONFIGS[specialist]
    route = router.resolve(AgentRole.SPECIALIST)
    return AgentRuntime(
        provider=route.provider,
        model=route.model,
        specialist=config.specialist,
        system_prompt=config.system_prompt,
        registry=registry,
        tool_names=config.tool_names,
        max_turns=config.max_turns,
        on_llm_response=on_llm_response,
    )
