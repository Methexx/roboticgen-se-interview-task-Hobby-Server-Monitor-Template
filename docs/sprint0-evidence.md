# Sprint 0 evidence

Status: IN PROGRESS. Observed on 2026-09-20, Ubuntu-24.04 under WSL2.
Baseline Git commit is e117407; these are uncommitted preparation changes.
This file is submission documentation and is intentionally tracked-eligible.

## Linux environment

`wsl -d Ubuntu-24.04 -- bash -lc 'cat /etc/os-release; uname -r; id;
ps -p 1 -o comm=; systemctl is-system-running; python3 --version'`:

- Ubuntu 24.04.5 LTS; kernel 6.6.87.2-microsoft-standard-WSL2.
- Default user methum-pc, uid 1000; systemd PID 1, system state running.
- Python 3.12.3; Git 2.43.0; no failed units on initial inspection.
- 16 logical CPUs, 7.6 GiB Linux-visible memory, 2 GiB swap.
- Root virtual filesystem reports 955 GiB available. This is not backing-host
  capacity: the earlier Windows check showed only about 35.4 GiB free on C:.
- No snaps, LXD CLI or LXD data directories were present initially.
- No Linux Node. The initial npm PATH entry pointed into Windows Program Files.

Installed via `apt-get update && apt-get install -y python3-venv python3-pip`.
APT also installed its recommended compiler/dev packages and updated dependencies;
these are development-environment costs, not measured application overhead.
Created a disposable feasibility venv at `~/projects/hsm-sprint0/.venv`.
Exact installed Python packages are in `scripts/sprint0/requirements.txt`;
`pip check` reported no broken requirements. Import/build/integration checks do
not constitute a dependency security audit.

Node v24.21.0 x64 came from the official nodejs.org release archive. Its SHA256
matched the release's HTTPS SHASUMS256.txt:
`fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6`.
Installed under `~/.local/share/hsm-tools/node-v24.21.0-linux-x64`; npm 11.19.0.
No shell startup files changed. Use its bin directory explicitly in PATH.
Astro 7.3.3 registry metadata requires Node >=22.12.0. The isolated static build
passed at 23:17:09 and passed again after `npm ci` at 23:19:52. The lockfile and
single test page are under `scripts/sprint0/astro-smoke`. Generated HTML was
inspected; this is not a browser workflow test. npm emitted an esbuild
allowScripts warning; no approval setting was changed.

## LXD startup issue

`snap install lxd --channel=5.21/stable` succeeded: 5.21.7-1018661,
revision 40585. Snap also installed core24 and refreshed its own snapd.
The first `lxc version` failed because `/snap/lxd/40585/meta/snap.yaml` was
missing in the WSL command's mount namespace. `systemctl status
snap-lxd-40585.mount` reports active/mounted through snapfuse, and
`nsenter -t 1 -m -- ls /snap/lxd/40585/meta/snap.yaml` succeeds. Thus installation
success is not yet usable CLI evidence. Requested a restart of only Ubuntu
to refresh WSL's mount namespace; user response pending. No storage, bridge,
project or container has been created or changed. Btrfs kernel module exists,
but storage capability/enforcement remains unverified.

## TinyFlux synthetic partition probe

Run from the repository using the Linux venv:

```bash
~/projects/hsm-sprint0/.venv/bin/python scripts/sprint0/tinyflux_probe.py --containers 1
~/projects/hsm-sprint0/.venv/bin/python scripts/sprint0/tinyflux_probe.py --containers 10
~/projects/hsm-sprint0/.venv/bin/python scripts/sprint0/tinyflux_probe.py --containers 10 --hours 1
```

Each is a fresh process, generating ten-second samples, writing a temporary
Linux-filesystem CSV, closing it, reopening and querying one container. It
asserts total count, queried count and final uptime. Timing includes query and
validation; peak RSS includes generation, write and read, reported by Linux
getrusage. One process owns the file throughout. Files are removed by the
temporary-directory context. These are synthetic probes, not real collection,
an API concurrency test, a month-long run or idle application measurements.

| Containers | Hours | Points | CSV bytes | Write s | Reopen/query/validate s | Peak RSS KiB |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 24 | 8640 | 4050424 | 0.524 | 0.281 | 48896 |
| 10 | 24 | 86400 | 40504240 | 3.525 | 2.037 | 237372 |
| 10 | 1 | 3600 | 1668380 | 0.180 | 0.116 | 27392 |

Decision: select hourly UTC partitions instead of daily partitions to reduce
per-file working memory and scheduling stalls. Whole-history query aggregation,
pruning and tick cadence still need implementation and load testing. At this
shape, ten containers project to about 1.2 GB per month without retention;
seven days of daily files would exceed the proposed 256 MiB byte cap. The cap
therefore can shorten retained history and must be visible to users.

## Actual corrections

- A Python metadata one-liner lost quotes through Windows/WSL command parsing;
  queried structured npm metadata from PowerShell instead.
- A composed PATH containing Windows spaces/parentheses broke a Bash command
  before execution. Retried with explicit Linux-only PATH for the probe process.
- Found the template's instruction to commit `.env`; corrected it to NEVER
  commit credentials, and changed its suggested bind address to loopback.
- A PowerShell bulk edit flattened single-pair replacement arrays and changed
  individual characters in four ignored reference files. Restored their text
  from the earlier reads, retained the intended hourly-partition revision, and
  reread the affected documents. Use explicit string mappings or patches for
  future reference edits. No application code was affected.

No application acceptance requirement has passed yet.
