"""Console entry point: ``harness`` (or ``python -m harness``)."""

import os

import uvicorn

from .api import DEFAULT_PORT
from .host_config import load_env_file


def main() -> None:
    # Antes de ler HARNESS_HOST/HARNESS_PORT: uvicorn.run recebe os dois por
    # argumento e não passa por create_app para descobri-los.
    load_env_file()
    host = os.environ.get("HARNESS_HOST", "127.0.0.1")
    port = int(os.environ.get("HARNESS_PORT", str(DEFAULT_PORT)))
    uvicorn.run("harness.api:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
