"""Console entry point: ``harness`` (or ``python -m harness``)."""

import os

import uvicorn


def main() -> None:
    host = os.environ.get("HARNESS_HOST", "127.0.0.1")
    port = int(os.environ.get("HARNESS_PORT", "8765"))
    uvicorn.run("harness.api:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
