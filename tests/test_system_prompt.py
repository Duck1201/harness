from harness import load_config
from harness.config import CapabilityConfig
from harness.system_prompt import build_system_prompt


def test_prompt_states_the_capability_the_profile_does_not_declare() -> None:
    config = load_config()

    prompt = build_system_prompt(config)

    assert "workspace root" in prompt
    assert "A tool result is authoritative" in prompt
    assert "Creating a file is a single write_file call" in prompt
    # The active profile declares vision as unknown, so the model is told it cannot see.
    assert config.runtime_profile.capabilities["vision"].support == "unknown"
    assert "You cannot see images" in prompt


def test_a_declared_capability_is_not_announced_as_missing() -> None:
    config = load_config()
    profile = config.runtime_profile
    seeing = profile.model_copy(
        update={
            "capabilities": {
                **profile.capabilities,
                "vision": CapabilityConfig(
                    support="supported",
                    evidence={"kind": "local_measurement", "source": "test"},
                    gate_status="passed",
                ),
            }
        }
    )
    profiles = config.model_profiles.model_copy(update={"runtime_profiles": (seeing,)})
    sighted = config.model_copy(update={"model_profiles": profiles})

    prompt = build_system_prompt(sighted)

    assert "You cannot see images" not in prompt
    assert "A tool result is authoritative" in prompt
