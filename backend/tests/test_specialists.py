from orchestrator.agents import SPECIALIST_CONFIGS, build_specialist
from orchestrator.config import Settings
from orchestrator.llm import ModelRouter
from orchestrator.schemas import SpecialistType
from orchestrator.tools import ToolRegistry
from tests.fakes import ScriptedProvider


def test_every_specialist_type_has_a_config() -> None:
    assert set(SPECIALIST_CONFIGS) == set(SpecialistType)


def test_prompts_mention_their_own_tools() -> None:
    # a prompt that never names its tools produces an agent that never uses them
    for config in SPECIALIST_CONFIGS.values():
        primary_tool = config.tool_names[0]
        assert primary_tool in config.system_prompt, (
            f"{config.specialist}: prompt doesn't mention {primary_tool!r}"
        )


def test_analysis_and_code_get_the_sandbox_not_the_web() -> None:
    for spec_type in (SpecialistType.DATA_ANALYSIS, SpecialistType.CODE_EXECUTION):
        tools = SPECIALIST_CONFIGS[spec_type].tool_names
        assert "run_python" in tools
        assert "web_search" not in tools  # least privilege per specialist


async def test_build_specialist_wires_runtime_end_to_end() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    provider = ScriptedProvider(["Findings saved.\nCONFIDENCE: 0.8"])
    router = ModelRouter(settings, {"anthropic": provider, "openai": provider})
    registry = ToolRegistry()  # empty registry: specs() just returns []

    runtime = build_specialist(SpecialistType.RESEARCH, router=router, registry=registry)
    result = await runtime.run("research X", subtask_id="s1")

    assert result.specialist is SpecialistType.RESEARCH
    assert result.output == "Findings saved."
    # routed through the specialist route (cheap model), not supervisor's
    assert provider.systems[0] is not None
    assert "research specialist" in provider.systems[0]
