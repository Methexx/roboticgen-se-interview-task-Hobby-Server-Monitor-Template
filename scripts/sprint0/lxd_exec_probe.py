"""Direct pylxd execution probe for the named Sprint 0 smoke container.

It observes websocket output handlers only. It intentionally does not claim a
deadline or process-termination mechanism: pylxd's execute signature has no
timeout argument on the pinned version.
"""

import json

from pylxd import Client


INSTANCE_NAME = "hsm-smoke-renamed"
PROJECT_NAME = "hsm"


def main() -> None:
    client = Client(project=PROJECT_NAME)
    instance = client.instances.get(INSTANCE_NAME)
    chunks: list[bytes] = []

    def on_stdout(chunk: bytes | str) -> None:
        chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)

    result = instance.execute(
        ["/bin/sh", "-c", "yes x | head -c 131072"],
        stdout_handler=on_stdout,
    )
    print(json.dumps({
        "container": INSTANCE_NAME,
        "project": PROJECT_NAME,
        "exit_code": result.exit_code,
        "handler_bytes": sum(map(len, chunks)),
        "handler_chunks": len(chunks),
        "returned_stdout_bytes": len(result.stdout or b""),
        "returned_stderr_bytes": len(result.stderr or b""),
    }, indent=2))


if __name__ == "__main__":
    main()
