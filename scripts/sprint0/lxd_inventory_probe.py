"""Read all local LXD projects and instances through pylxd.

This is a read-only Sprint 0 feasibility probe. It does not choose an app ID,
authorize any caller, or mutate LXD.
"""

import json

from pylxd import Client


def main() -> None:
    root_client = Client()
    inventory = {}
    for project in root_client.projects.all():
        project_client = Client(project=project.name)
        response = project_client.api.instances.get(params={"recursion": 1})
        response.raise_for_status()
        inventory[project.name] = [
            {"name": instance["name"], "status": instance["status"],
             "uuid": instance["config"].get("volatile.uuid")}
            for instance in response.json()["metadata"]
        ]
    print(json.dumps(inventory, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
