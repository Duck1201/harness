"""Builds the system prompt from the contracts, instead of hand-written prose.

Two things the model kept getting wrong are things the harness already knows and
never said. Asked to read an image, it spent six calls searching the workspace
and the web for a file that does not exist, because nothing told it the session
has no vision — while ``config/model-profiles.json`` has declared exactly that
all along. And after picking the right tool it called another one to check the
answer, because nothing told it a ResultPayload is the answer.

So the prompt is generated: the capability lines come from the active
RuntimeProfile, and drift between what the contract declares and what the model
is told becomes impossible rather than merely unlikely.
"""

from collections.abc import Mapping

from .config import HarnessConfig

_BASE = (
    "Use the available tools when needed. All workspace paths supplied to tools "
    "must be relative to the workspace root."
)

# A tool result is the answer, not a claim to be checked. Re-reading what a
# previous result already reported is the single most common way a Turn spends
# its budget without learning anything.
_RESULT_AUTHORITY = (
    "A tool result is authoritative. Do not call another tool to confirm what a "
    "result in this turn already reported: if a search listed the files, that is "
    "the list; if an edit reported success, the file changed."
)

# Only capabilities whose absence changes what the model should do. Streaming and
# parallel tool calls are harness concerns and would be noise in the prompt.
_MODEL_FACING_LIMITS: Mapping[str, str] = {
    "vision": (
        "You cannot see images. If a request depends on looking at one, say so "
        "plainly instead of searching the workspace or the web for it."
    ),
}


def build_system_prompt(config: HarnessConfig) -> str:
    """The prompt for the active RuntimeProfile, capability lines included."""
    capabilities = config.runtime_profile.capabilities
    limits = [
        sentence
        for name, sentence in _MODEL_FACING_LIMITS.items()
        if (capability := capabilities.get(name)) is None or capability.support != "supported"
    ]
    return " ".join([_BASE, _RESULT_AUTHORITY, *limits])


__all__ = ["build_system_prompt"]
