# Final Report — Hobby Server Monitor

## Sprint 0 continuation — Linux feasibility

The user completed Ubuntu account setup. At 23:11:47 +05:30, read-only checks
verified Ubuntu 24.04.5, systemd running and Python 3.12.3. Installed venv/pip
tools and LXD 5.21/stable (5.21.7, revision 40585), then Linux Node 24.21.0 with
verified release checksum. Astro 7.3.3 built successfully and repeated the build
after a locked `npm ci`. Python `pip check` passed. Probe dependencies are pinned
separately from the still-unimplemented application manifests.

LXD discovery is blocked by a Snap mount namespace mismatch: systemd sees its
mounted files, while the WSL CLI process does not. An Ubuntu-only restart is
pending user approval because it closes the user's open Ubuntu shell. No LXD
storage, bridge, project or container has been created. No Windows reboot is
currently indicated.

Synthetic TinyFlux probes measured daily and hourly partitions. For ten
containers, process peak RSS fell from 237372 KiB (24h) to 27392 KiB (1h);
reopen/query/validation fell from 2.037s to 0.116s. Selected hourly partitions,
updating the local design references. These are isolated synthetic measurements,
not production footprint, retention or collection-cadence proof.

Commands, results, dependency costs and limitations are in
[Sprint 0 evidence](docs/sprint0-evidence.md) and
[probe instructions](scripts/sprint0/README.md). Codex wrote and ran the probes,
corrected command quoting issues and repaired an erroneous bulk text replacement
in private references before rereading them. It also corrected the template's
unsafe instruction to commit `.env` and changed its bind example to loopback.
No secrets were requested, configured or committed.

Observed continuation interval: 23:11:47–23:19:52 +05:30 (8m05s, including
installation/build waits, excluding subsequent documentation). Active effort
has not been measured separately. Sprint 0 remains incomplete; later sprints
have not started. No application acceptance rows have been marked passed.

## Sprint 0 continuation — LXD integration feasibility

The user directed a restart of Ubuntu-24.04 only. It resolved Snap's mount
namespace mismatch; Docker Desktop was not terminated. The local user was added
to LXD's root-equivalent group and Ubuntu was restarted again so group membership
applied. LXD 5.21.7 then discovered a fresh server with no storage, networks or
instances. Service/socket inspection showed no daemon errors before setup.

Initialized only this new LXD installation using an explicit 12 GiB loop-backed
Btrfs pool and private bridge. Created the dedicated `hsm` project and the
known test-only `hsm-observe` project. The `hsm-smoke-renamed` container is
unprivileged, has RAM/CPU/process/disk limits, and its 4 GiB disk quota rejected
a bounded 5 GiB write at 3171811328 bytes; the test file was deleted and cleanup
verified. Its UUID persisted across a stopped rename/restart. Cross-project
inventory works using a read-only pylxd raw API probe.

Direct pylxd exec streamed 128 KiB via five handlers. Its execute API has no
timeout argument; a CLI cancellation observation is not sufficient termination
proof for the eventual app. The tracked evidence has exact commands, outputs,
library warnings, limitations, and the known test-resource names. Sprint 0
continues with configuration/migration and OAuth feasibility; no later sprint
workflow is claimed complete.

## Sprint 1 — HTTP foundation

Implemented the Falcon application factory, request IDs, JSON errors, and an
explicit route-and-method policy registry. `GET /healthz` is the only declared
public endpoint. A missing path is denied with a structured 404 response, and
an undeclared method on a declared path returns structured 405. The registry
rejects startup when a responder lacks a declared policy.

Nine focused tests passed for health access, default-deny behavior, policy
coverage, configuration, and migration. The first Falcon implementation used a
nonexistent application `context` field; focused tests exposed it, so settings
remain constructor-injected until resources need explicit dependencies. Error
codes initially included Falcon's numeric prefix and were normalized to the API
contract. No OAuth, session, container, collector, or browser workflow is
claimed implemented.

## Sprint 0 evidence — 2026-09-20 (in progress)

## Sprint 1 — control-plane migrations

Added append-only SQLite migrations for the application state required by the
planned workflows: users, opaque sessions, browser-bound OAuth transactions,
bootstrap settings, stable-ID containers and assignments, latest metrics,
collector status, operation intents, quota reservations, history jobs, and
audit records. Each connection enables foreign keys and receives a bounded busy
timeout; each pending migration is applied under `BEGIN IMMEDIATE` and recorded
only after its statements succeed. This gives a deterministic local schema
upgrade path without claiming an atomic transaction with LXD.

Ten focused tests passed in Ubuntu Python 3.12: the prior HTTP/configuration
checks plus fresh-database idempotency and an upgrade from a deliberately
created version-1 database. The host PowerShell Python 3.14 lacked the pinned
dependencies and does not meet the declared interpreter range, so verification
uses an ignored Ubuntu virtual environment with the project source on
`PYTHONPATH`. OAuth, session handling, collector activity, browser login, and
any mutation of LXD resources remain unimplemented.

Baseline commit: `e117407`. No application source or functional tests exist yet.
## Sprint 1 — collector boundary skeleton

Added a separate collector module and one-shot entry point. It has no LXD
client, no scheduler, and no destructive behavior. Its SQLite-only heartbeat
records that the process is idle; its latest-snapshot path upserts supplied data
only for an existing stable inventory ID, where the database foreign key
enforces that boundary. Two focused tests passed without accessing LXD or any
preserved test container. Continuous polling, TinyFlux writes, recovery, and
service installation are still unimplemented.

## Sprint 1 — Astro browser state slice

Replaced the template dashboard placeholder with a pinned Astro 7.3.3 static
application. It builds a sign-in page and a dashboard placeholder with explicit
loading, empty, stale, and error states; `?state=` selects each state in the
browser. It has no API call, session, OAuth redirect handler, or claim of an
authenticated user. Ubuntu Node 24.21.0 was restored from the official archive
after read-only discovery found only the Windows npm shim. The archive checksum
matched its published SHA-256. Astro built two static pages successfully.

An offline `npm ci` attempt against the OneDrive checkout exceeded a 55-second
cap while extracting cached packages and was terminated. The build therefore
used the already verified Sprint 0 Astro dependency tree through a local ignored
symlink, then the symlink was removed. The committed lockfile was regenerated
offline from that same pinned dependency set. This is a local filesystem
performance limitation, not evidence of a clean frontend installation.

## Sprint 2 — user, quota, and stable-ID access domain layer

Implemented the SQLite domain services behind later user and container APIs.
They normalize invitations, protect the last active admin from demotion or
revocation, delete sessions and assignments on revocation, and charge the
distinct set of containers a user owns or is assigned. Assignment, ownership
transfer, quota reduction, and prospective limit increases validate the
configured allocation rather than telemetry. Stable application container IDs
are resolved centrally before any future LXD project/name lookup; an existing
but unassigned ID returns 403 for a Container User. Twenty focused backend
tests pass. No authenticated HTTP endpoint, OAuth login, LXD mutation, or
browser user-management flow is claimed complete yet.

## Sprint 2 — opaque session foundation

Added opaque random browser tokens stored as SHA-256 digests, with configured
idle and absolute expiry. Protected policy routes resolve only active users,
which makes a revoked user’s next request fail even if a cookie remains in the
browser. Focused tests cover digest-only persistence, expiry deletion, revoked
session rejection, and anonymous/non-admin denial at middleware. OAuth has not
yet issued these sessions, and no login/browser flow is claimed.

The original backend placeholder was inspected and retained. The dashboard
placeholder was later replaced by the Sprint 1 Astro state slice described
above.
All required application workflows remain NOT VERIFIED; no later sprint started.

Repository preparation:

- Added exact root-anchored ignore rules for AGENTS.md, START-HERE.md and the
  twelve supplied numbered planning files. `git ls-files -- AGENTS.md
  START-HERE.md docs` returned no entries, so no index removal was needed.
- `git check-ignore -v` matched all fourteen references. `git check-ignore
  --no-index -v -- README.md REPORT.md docs/future-submission.md` returned 1
  (no matches). The docs directory itself is not ignored.
- Added local `.env` exclusions with an explicit `.env.example` exception before
  credentials are configured. No credentials were requested or read.
- No staging, commits, pushes, email, LXD initialization or container changes.

Initial read-only observations (2026-09-20, approximately 23:02–23:04 +05:30):

| Check | Actual result |
| --- | --- |
| Windows CIM OS query | Windows 11 Pro, 10.0.26200, build 26200 |
| `wsl --version` | WSL 2.6.1.0; kernel 6.6.87.2-1; Windows 10.0.26200.9445 |
| `wsl --status` | Default distribution docker-desktop; default version 2 |
| `wsl --list --verbose` | Only docker-desktop, Stopped, VERSION 2 |
| `wsl --list --online` | Ubuntu-24.04 available |
| Windows `python --version` / `py --version` | Both Python 3.14.2 |
| Windows `node --version` / `npm.cmd --version` | v24.11.1 / 11.6.2 |
| CIM C: capacity / free bytes | 510862278656 / 38037004288 (snapshot, not reserved capacity) |
| Windows command discovery for lxc/lxd | Neither found on PATH; this does not establish Linux availability |
| Ubuntu, systemd, Linux Python/Node, LXD | Not verified: no Ubuntu distro registered |

The sandbox denied Windows CIM/WSL inventory and could not execute the Python
aliases. Approved read-only retries outside the sandbox succeeded. This was an
execution-access limitation, not proof that WSL or Python was absent. The
sandbox disk query was also inconclusive; the CIM retry supplied the figures above.

Decision: install a separate Ubuntu-24.04 WSL2 environment, then use its Linux
filesystem. Reusing Docker Desktop's internal distro would couple development to
unrelated container tooling. Inspect actual Linux services and LXD resources
before installing packages or allocating storage. Available Windows space is
about 35.4 GiB; an LXD pool budget still requires Linux capacity checks.

Approved host change: `wsl --install -d Ubuntu-24.04 --no-launch` exited 0
and reported success. At 23:08:18 +05:30, `wsl --list --verbose` confirmed
Ubuntu-24.04 Stopped / VERSION 2; docker-desktop remained the default,
Stopped / VERSION 2. No reboot was requested. Linux first-run account creation
is the next local step (`wsl -d Ubuntu-24.04`); no password is to be shared.
Installation does not yet verify systemd, Linux dependencies or LXD.

Pending Sprint 0 gates: Ubuntu first-run account and systemd; tested/pinned Linux
dependencies; LXD discovery and enforced RAM/CPU/disk limits; cross-project
inventory and rename identity; bounded exec prototype; TinyFlux partition cost;
local OAuth setup and callback reachability; config/migration skeleton. Windows
tool versions alone do not prove target compatibility. No CPU/RAM footprint or
application benchmark has been measured.

AI assistance: Codex inspected the repository and local reference pack, edited
ignore rules and these status notes, and ran the listed checks. Its initial
access errors were corrected by approved retries; no generated application code
has been accepted or tested. Exact active effort before the first clock capture
at 23:02:24 +05:30 was not recorded; time totals remain pending rather than invented.
The observed interval to registration verification was 5 minutes 54 seconds,
including tool approvals and installation waiting, not total active effort.

The template brief below/README contains August deadlines while the private
reference pack reports a September extension; the underlying deadline email was
not present in this checkout. This discrepancy does not block environment work.

> Delete this instruction block and fill in the sections below. Be specific
> and honest. See the "Final Report" and "Decisions we are leaving to you" sections
> of [README.md](README.md) for what's expected here.

## Time Spent

Rough breakdown, not a timesheet.

| Area | Time |
| --- | --- |
| Backend (API, auth, authorization) | |
| Dashboard (Astro frontend) | |
| LXD integration | |
| Background collector / TSDB | |
| Debugging | |
| Documentation / report | |
| **Total** | |

## Key Decisions

Pick at **more than three** of the open questions from README.md's "Decisions we are
leaving to you" and answer them properly: what you chose, the alternatives
you considered, and why you rejected them.

## Issues Encountered and Solutions

The real ones — including what you tried first and got wrong.

## What You Learned

## Bonus Features Implemented

## Resource Measurements

Idle RAM and CPU footprint of the collector (and the API, if also running
continuously), and exactly how you measured it (tool, command, duration,
conditions).

## Known Limitations

What is unfinished, broken, simulated, or deliberately cut — and why.

## AI Tool Usage

Which tools, for which parts of the system, what you accepted as-is, and
what you rejected or had to fix. You should be able to explain every line
you submit, AI-assisted or not.
