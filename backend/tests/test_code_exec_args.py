"""Unit tests for the sandbox policy — no docker needed."""

from orchestrator.tools.builtin.code_exec import build_docker_args


def test_sandbox_policy_flags_present() -> None:
    args = build_docker_args("sbx-test")

    assert "--network=none" in args
    assert "--read-only" in args
    assert "--memory=256m" in args
    assert "--pids-limit=128" in args
    assert "--rm" in args
    # runs as nobody, not root
    user_idx = args.index("--user")
    assert args[user_idx + 1] == "65534:65534"


def test_code_arrives_via_stdin_not_argv() -> None:
    # code in argv would leak into `docker inspect` / process lists
    args = build_docker_args("sbx-test")

    assert args[-1] == "-"
    assert "-i" in args
