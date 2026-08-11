from pathlib import Path

from harness import HarnessConfig, ReplayPolicy, load_config


def test_config_loader_reads_harness_profiles_and_tool_registry() -> None:
    config = load_config(Path("config/harness.json"))

    assert isinstance(config, HarnessConfig)
    assert config.loop.max_steps == 15
    assert config.loop.max_output_tokens == 8192
    assert config.default_execution_route == "local_web_tools"
    assert config.execution_route.id == "local_web_tools"
    assert config.runtime_profile.model.id == "mitos:latest"
    assert (
        config.runtime_profile.profile_digest_sha256
        == "79d1056f6cb28d32c470a2206e76c83985dacabcac6bc141d088c15d344817f4"
    )
    assert config.context.initial_budget_tokens == 32768
    assert [tool.name for tool in config.tool_registry.model_tools] == [
        "read_file",
        "write_file",
        "edit",
        "list_directory",
        "glob",
        "grep_search",
        "web_search",
        "web_fetch",
    ]
    assert config.tool_schemas[0].parameters["type"] == "object"
    assert (
        config.tool_registry.model_tools[0].replay_policy
        is ReplayPolicy.NEVER_CACHE_WORKSPACE_READS
    )
