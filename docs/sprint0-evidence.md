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

## LXD startup and feasibility

`snap install lxd --channel=5.21/stable` succeeded: 5.21.7-1018661,
revision 40585. Snap also installed core24 and refreshed its own snapd.
The first `lxc version` failed because `/snap/lxd/40585/meta/snap.yaml` was
missing in the WSL command's mount namespace. `systemctl status
snap-lxd-40585.mount` reports active/mounted through snapfuse, and
`nsenter -t 1 -m -- ls /snap/lxd/40585/meta/snap.yaml` succeeds. Thus installation
success was not yet usable CLI evidence. After the user directed an Ubuntu-only
restart, `wsl --terminate Ubuntu-24.04` succeeded and Docker Desktop was not
terminated. The subsequent `lxc version` reported client/server 5.21.7 LTS.
The regular user initially received a Unix-socket permission denial, as expected
before membership in the root-equivalent `lxd` group. After `usermod -aG lxd
methum-pc` and another Ubuntu-only restart, normal user discovery succeeded.

Daemon/log inspection before configuration found socket activation listening and
no daemon journal errors. Root discovery confirmed a fresh server: default
project only, no storage pools, no managed networks, no instances and an empty
default profile. That allowed initialization without reinitializing existing LXD
resources. No Docker Desktop configuration or containers were changed.

LXD was initialized through an explicit preseed with `hsm-btrfs`, a 12 GiB
loop-backed Btrfs pool at `/var/snap/lxd/common/lxd/disks/hsm-btrfs.img`, and
private bridge `hsmbr0` (10.70.0.1/24, IPv4 NAT, IPv6 disabled). The default
profile provides only that root disk and NIC. `lxc info` reports Btrfs 6.6.3 and
the Btrfs kernel module is loaded. The Windows backing C: volume had about 31.7
GiB free after initialization; this is the relevant physical constraint, not
the WSL virtual filesystem's approximately 953 GiB free figure.

Created `hsm` with shared profiles/images disabled and created one separately
owned `hsm-observe` project solely for cross-project discovery testing. The
running `hsm-smoke-renamed` instance in `hsm` has 512 MiB RAM, one CPU affinity,
256 processes, `security.privileged=false`, `security.nesting=false`, and a
4 GiB root volume. The stopped `hsm-observe/inventory-smoke` instance has 256
MiB, one CPU, 128 processes and a 2 GiB root volume. Both use the managed
network/profile and are known test resources.

Container cgroup files reported `memory.max=536870912`, `pids.max=256`, and
one effective CPU (`cpuset.cpus.effective=2`) for the running smoke instance.
Its configured CPU uses affinity, so `cpu.max` remains `max 100000` rather than
a time quota. A bounded named-file disk test attempted 80 × 64 MiB zero blocks;
it failed with `Disk quota exceeded` after 3171811328 bytes. The test file was
removed, checked absent, and the filesystem returned to its pre-test used space.
Host C: free space returned from 32122986496 to 32123052032 bytes. This verifies
the container volume limit without exhausting host storage.

The instance's LXD `volatile.uuid` was `356f06b8-cbcb-4146-aa45-44d4e74fdada`
before and after stopping, renaming from `hsm-smoke` to `hsm-smoke-renamed`, and
restarting. LXD refuses live renames; the stopped rename is the tested behavior.
`lxc list --all-projects` and the read-only pylxd probe see the hsm instance and
the stopped hsm-observe instance, while default remains empty.

For pylxd 2.4.2, the model collection returned names suffixed with `?project=…`
in this setup. The raw project-scoped API response with `recursion=1` returned
canonical names and UUIDs, so the probe uses that path. This is a compatibility
observation, not authorization logic.

Terminal feasibility: `Instance.execute` has streaming handlers but no timeout
parameter. A direct handler received 131072 bytes in five chunks from the smoke
container. In contrast, the Windows/WSL CLI pipeline observed only 65536 of a
131072-byte command stream. A host `timeout 3` cancelled `lxc exec sleep 20`
with exit 124; `pgrep sleep` then returned 1, meaning no sleep process remained.
This CLI cancellation observation does not prove a future application control
channel terminates every command; that remains a terminal implementation gate.
The direct pylxd probe emitted a warning that its Operation model did not know
the `requestor` field, recorded as an installed-version behavior.

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

## Configuration and migration skeleton

`backend/pyproject.toml` pins the direct Python runtime dependencies used by the
selected stack and supports a Python 3.12-only tested environment. The new
typed configuration module reads only documented values and rejects missing
Google/bootstrapping fields, unsafe non-loopback HTTP, an invalid boolean, or a
collector cadence other than the required ten seconds. It contains no `.env`
loader, so manual development must load the ignored file deliberately.

`scripts/init_db.py --database PATH` calls the initial, versioned SQLite
migration. The deliberately small v1 schema has only `schema_migrations` and
`app_settings`; the tables needed for future routes will be added by subsequent
migrations rather than pretending Sprint 1 exists. Every connection enables
foreign keys, WAL, and a five-second busy timeout. `backend/tests` passed four
tests: local callback/default parsing, non-loopback HTTP rejection, cadence
rejection, and idempotent migration with foreign keys enabled. The init script
was also run twice against `/tmp/hsm-sprint0-init-20260920.sqlite`; both runs
succeeded and the named temporary database, WAL, and SHM files were removed.
Editable installation from `backend/pyproject.toml`, compilation, and the same
four tests succeeded in the Sprint 0 venv. This is initialization/configuration
evidence only, not an API or authentication implementation.

## OAuth prerequisite still pending

No Google OAuth client, secret, bootstrap email, or callback was configured or
read. Create the Web OAuth client locally with exact redirect URI
`http://localhost:8000/auth/callback`, scopes `openid email profile`, and test
users while the application is in testing. Put values only in an ignored `.env`
file. Callback reachability cannot be tested until a future Sprint 1 API serves
the route; it is correctly still unverified.
