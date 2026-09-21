# Software Engineer Intern Task — Hobby Server Monitor

## Implementation status — Sprint 0

Repository preparation and environment discovery began on 2026-09-20. The
repository still contains the original brief below and placeholder manifests;
there is no runnable application yet. See REPORT.md for observed versions,
commands, blockers and verification status. Private local planning files are
ignored; submitted architecture, security, API and setup documentation will be
maintained here as implementation is verified.

Ubuntu-24.04 was installed and verified as WSL2 on 2026-09-20 alongside Docker
Desktop. No reboot was requested. Linux account creation is complete. Launch it:

```powershell
wsl -d Ubuntu-24.04
```

On a fresh setup, create the Linux username/password locally; never put passwords
or OAuth secrets in chat. Then verify the distribution with `wsl --list --verbose`
and, inside Ubuntu, `cat /etc/os-release`, `ps -p 1 -o comm=`, and
`python3 --version`. LXD discovery and storage feasibility must precede
initialization or container tests. Do not use Docker Desktop's internal distro
for this project or reinitialize existing LXD storage.

For a fresh machine, first run `wsl --install -d Ubuntu-24.04 --no-launch`;
administrator privileges and a reboot may be required if Windows requests them.
The remaining steps are pending, not a successful setup rehearsal. Use a Linux
filesystem checkout for development after Ubuntu is ready; the current checkout
is in Windows OneDrive. Keep credentials in ignored `.env` files or protected
Linux configuration outside Git; `.env.example` remains tracked.

Reference: [Microsoft WSL installation guidance](https://learn.microsoft.com/en-us/windows/wsl/install).

Current Linux checks: systemd running, Python 3.12.3, Linux Node 24.21.0, and
Astro 7.3.3 build verified. LXD 5.21.7 is running after an Ubuntu-only restart;
the fresh test server uses a 12 GiB loop-backed Btrfs pool and private bridge.
The real 4 GiB container disk quota was verified. [Sprint 0 evidence](docs/sprint0-evidence.md) and
[probe instructions](scripts/sprint0/README.md) document reproducible checks.
TinyFlux probes selected hourly UTC partitions to reduce measured per-file
memory use; no production collector or API exists yet.

### Sprint 1 status

The initial Falcon API now exposes only `GET /healthz`. Every registered route
and HTTP method must have an explicit policy; undeclared paths are denied and an
undeclared method receives a structured 405 response. Each response receives a
server-generated `X-Request-ID`. Authentication, OAuth callbacks, inventory,
and collection remain unimplemented at this point.

SQLite migrations now advance an empty or existing version-1 database to the
control-plane schema used by later work: identities and sessions, OAuth
transactions, bootstrap settings, stable container inventory and assignments,
latest snapshots, collector status, operation intents, quota reservations,
history jobs, and audit records. Migrations are ordered, recorded, idempotent,
and executed with foreign-key enforcement on each connection. No login,
collection, or LXD mutation has been demonstrated.

The independent collector skeleton currently has no LXD client and starts no
continuous loop. Its one-shot service process writes a SQLite heartbeat and can
upsert an explicitly supplied latest snapshot for an existing stable inventory
ID. This establishes the API/collector process boundary without touching the
preserved LXD test resources or claiming collection works.

The first Astro browser slice provides a static sign-in page and an
authenticated-dashboard placeholder. The dashboard explicitly renders loading,
empty, stale, and error states selected with `?state=loading|empty|stale|error`.
It does not claim an authenticated session or live inventory; those states are
ready for the later SQLite snapshot API.

The Sprint 2 domain layer now enforces invitation normalization, active-admin
protection, revocation cleanup, configured allocation quotas, and stable-ID
container access. A Container User needs an explicit assignment; an existing
unassigned container ID is forbidden. These services are verified against
temporary SQLite databases but are not yet HTTP endpoints or a demonstrated
browser workflow.

Opaque browser-session handling now stores only SHA-256 token digests and
requires an active user on protected route policies. Sessions enforce idle and
absolute expiry; user revocation takes effect on the next request. Google OAuth
does not create sessions until its callback is implemented and tested.

Admin-only JSON endpoints now list users, invite by email, change role/quota,
revoke users, and add or remove container assignments. They reject unknown JSON
fields and are protected by the explicit policy registry. The browser pages and
OAuth callback are not wired to these endpoints yet.

Authenticated container-list and detail endpoints read the latest SQLite
snapshots only. Administrators can list the current inventory; Container Users
receive only explicit assignments, and the central stable-ID check rejects an
existing unassigned container with 403 before it is resolved further. These are
not LXD polling endpoints.

Authenticated callers can now read their server-calculated identity, quota,
allocated resources, and remaining budget through `/api/me` and `/api/me/quota`.
The latter currently reports no pending reservations because creation operations
have not been implemented.

### Google OAuth prerequisite

Sprint 0 has not verified Google login or callback reachability because no API
callback endpoint exists yet. Locally, create a Google **Web application** OAuth
client, configure the exact development redirect URI
`http://localhost:8000/auth/callback`, request only `openid`, `email`, and
`profile`, and add the intended accounts as test users while the consent screen
is in testing. Copy its client ID, client secret, and your verified bootstrap
email into an ignored `.env` created from `.env.example`; do not put them in Git
or chat. The configuration loader validates the values when the future API starts.

**Contact:** dev@roboticgen.co

---

## 👤 Role

You are a **Software Engineer Intern**. There is an old Linux machine in the office, and your job is to turn it into an on-premises server for testing purposes.

You will use **LXD** (documentation) to manage containers on that machine, and you will build an **admin dashboard and control panel** on top of it so the server can be managed and monitored from a browser.

Two constraints run through the whole task and are weighted heavily in evaluation,

1. **Resource efficiency** — the tool runs *on* the server it monitors. It must consume as little CPU and RAM as possible while still doing its job.
2. **Security** — this is a server with container-level privileges. Authentication, authorization, and input handling must be done correctly.

---

## 📋 Prerequisites

- **WSL2 (Windows) or a native Ubuntu machine (preferred)** to test LXD functionality. LXD does not run on macOS natively; use a VM if needed.
- Python 3.10+
- Node.js 18+ (for Astro)
- LXD 5.x installed and initialized (`lxd init --minimal` is enough for development)
- A Google Cloud project with OAuth 2.0 credentials (free tier without adding a card)

---

## 📘 Project Summary

### Users

Two roles,

| Role | Capabilities |
| --- | --- |
| **Admin** | Create, update, view and delete containers. Manage users and container assignments. Full terminal access. |
| **Container User** | View metrics for containers explicitly assigned to them. Terminal access to those containers only. |

### Authentication

- Users sign in via **Google OAuth 2.0**.
- The first user to sign in becomes the Admin, **or** a bootstrap admin email is set via environment variable. Document whichever you choose.
- Users who have never been invited by an Admin must not gain access by simply signing in with a Google account.

---

## 🧭 System Flow Overview

### 🔄 Main Process

**1. Admin logs into the admin dashboard**

1.1 The dashboard shows **all containers** with key metrics. A starting set, decide for yourself what else earns space on the screen and what is noise,

- CPU usage (%)
- RAM usage (used / allocated, %)
- Disk usage (used / allocated, %)
- Network I/O (RX / TX bytes and rate)
- Container state (Running / Stopped / Frozen / Error)
- Uptime
- Process count
- Image / OS version
- IPv4 address

1.2 Admin can **create a new container** through an interactive form. Roughly what it needs to cover (treat this as a starting point rather than a checklist),

- Container name (validated - lowercase alphanumeric + hyphens, LXD naming rules)
- Base image / Ubuntu version (selected from a list of available aliases)
- **RAM allocation** — slider with enforced min/max bounds
- **CPU allocation** — slider or stepper for core count, plus CPU allowance (%)
- **Disk size** — slider, bounded by available storage pool capacity
- Network configuration (available bridges / profiles, discovered at runtime)
- Storage pool selection
- Ephemeral toggle
- Autostart on boot toggle
- Optional description

Bounds must be derived from the **actual host capacity and the requesting user's remaining quota**, not hard-coded, and the API must re-validate every value server-side. Explore `pylxd` for further configurable options and add what makes sense. Make sure you document what you added and why.

1.3 Admin can **update or delete** an existing container,

- Start / stop / restart / freeze
- Change resource limits on a running container
- Delete, with a confirmation step
- Destructive and limit-changing actions leave a trail

1.4 Admin can **add users** to the organization and grant them access to one or more containers,

- Invite by Google account email
- Assign / revoke container access
- Change a user's role
- Revoke a user entirely
- **Set a resource quota per user** — maximum RAM, CPU cores, and disk that user's containers may collectively consume

1.5 Admin can see **usage accounting** across the server,

- Total allocated vs. total available RAM, CPU and disk for the host
- Per-user allocated total against that user's quota
- Per-container consumption over a selectable period, derived from the TSDB

**2. Container User**

2.1 Sees metrics **only** for containers assigned to them. Requesting an unassigned container by ID must return 403.

2.2 Sees their own quota and how much of it is currently allocated.

### Common

Both roles can access a **terminal** for containers they have access to: send a command from the UI, see the result. A real `pylxd` exec is preferred.

This feature lets a logged-in user run text you create on a machine you control. A key challenge is making sure this can't be used to gain control of the host machine, and we'll ask how you would prevent that.

### 🔄 Background Process

1. Poll resource metrics for every container **every 10 seconds**.
2. Store the samples in a **TSDB (TinyFlux)**.
3. The collector keeps working when LXD is temporarily unavailable, and its cost does not scale with the number of open browser tabs.
4. Storage growth is bounded. A collector left running for a month on a small server should not be a problem — how you achieve that is up to you.

---

## 🛠️ Tech Stack

| Layer | Tech (Preferred) |
| --- | --- |
| Backend | Falcon |
| Dashboard | Astro |
| LXD | pylxd |
| DB | sqlite3 |
| TSDB | TinyFlux |

Substitutions are allowed only with justification in the final report. Choosing a familiar heavyweight framework because it is familiar is not a justification.

---

## 🎯 Scope & Expectations

This section describes **what the tool has to accomplish**, not how to build it. That is deliberate. We are not handing you a specification you can implement line by line, because deciding what to build, what to leave out, and being able to justify both is most of the task.

### The baseline

The tool is not finished until someone can, from a browser,

- Sign in with Google and land in a view appropriate to their role.
- As an Admin, see every container on the host with live resource metrics.
- Create a container through a form that only offers valid options.
- Change limits on, restart, and delete an existing container.
- Invite a user, give them a resource budget, and grant them access to specific containers.
- Sign in as that user and see only what they were given.
- Look at how a container behaved over the last minutes and hours, not only right now.
- Run a command against a container and see the output.

Underneath that, a background process keeps collecting metrics whether or not a browser is open, and history survives both a page refresh and a service restart.

That is the floor, not the target. How much further you go is your call.

### Decisions we are leaving to you

Each of these has more than one defensible answer. We care much more about *why* you chose one than about which one you chose:

1. How does a signed-in user stay signed in, and what does logging out actually do?
2. Where in your system does an authorization decision get made, and how do you stop an endpoint written next month from missing it?
3. What exactly does a quota measure, and what happens at the moment someone reaches theirs?
4. `pylxd` needs privileged access to LXD. What does that access actually grant, and what did you do about it?
5. How does the dashboard find out that something changed, and what does that cost while nobody is looking at it?
6. What is in your metric store after a month of uptime?
7. How real is your terminal, and what can an authenticated user do with it that you did not intend?
8. What does your schema do when a container is renamed, and when one is deleted while still assigned to someone?
9. How much data does a 24-hour chart need to move, and how much of it reaches the browser?
10. What does a user see when LXD is down, slow, or answers with something you did not expect?
11. How does the first Admin come to exist, and why can that mechanism not be abused?
12. How does this run on the machine, and how does it come back after a reboot?

We would rather read three of these answered thoughtfully, with the rest listed honestly as known gaps, than twelve answered with whatever the first suggestion happened to be.

### Non-negotiables

Short list, and we do check these directly,

- The metrics collector runs independently of the UI.
- You measured your resource footprint and published the numbers.
- You can **explain and defend every line you submit.**

### If you run out of time

Cut deliberately and say so. A smaller, coherent system with a clear-eyed "here is what I did not build and why" reads far better than six half-wired features.

---

## 📦 Expected Deliverables

### 🔹 Source Code

- A public or shared Git repository containing backend, frontend, and any scripts.
- Meaningful commit history — incremental commits, not one commit titled "final".
- `.env.example` listing every required environment variable.
- Dependency manifests (`requirements.txt` / `pyproject.toml`, `package.json`).
- A working `init_db` / migration path so a reviewer can start from an empty machine.

### 🔹 Documentation

A `README.md` containing but not limited to,

1. **Setup** — from a fresh Ubuntu/WSL machine to a running app, including LXD init and Google OAuth credential setup. A reviewer should reach a running dashboard by following it, with no undocumented steps.
2. **Architecture** — a diagram plus a short explanation of how the components talk to each other.
3. **Data model** — SQLite schema and the TinyFlux measurement/tag/field layout.
4. **API reference** — endpoints, methods, required role, request/response shape.
5. **Security notes** — threat model summary and your LXD privilege decision.
6. **Configuration** — every environment variable and what it does.

### 🔹 Final Report

A separate `REPORT.md` including,

- **Time spent** — roughly per area (backend, frontend, LXD integration, debugging).
- **Issues encountered and solutions** — the real ones, including what you got wrong first.
- **What you learned.**
- **Bonus features implemented.**
- **Resource measurements** — your idle RAM and CPU footprint, and how you measured it.
- **Known limitations** — what is unfinished, broken, or simulated.
- **AI tool usage** — see below.

> Use of AI tools (e.g. ChatGPT, GitHub Copilot, Cursor, Claude) is **highly encouraged**.
> 
> 
> Be transparent about how they were used by documenting it in your report: which tools, for which parts, what you accepted, and what you rejected or had to fix. You must be able to explain every line of code you submit and expect to be asked during the review.
> 

---

## ✅ Evaluation Criteria

Submissions are reviewed in two parts: we set the project up and read the code, then we sit with you for **30–45 minutes** and ask about it.

| # | Area | Weight | What we look for |
| --- | --- | --- | --- |
| 1 | **Judgment & defense of decisions** | 25% | Did you make real choices from the open list above, and can you explain the tradeoffs and the alternatives you rejected? |
| 2 | **Security & correctness** | 20% | Authorization enforced at the API, input validated server-side, no injection paths, no leaked secrets, and a reasoned position on the LXD privilege problem. We probe these directly rather than reading about them. |
| 3 | **Functional completeness** | 20% | Does the baseline work end to end on a clean machine, following only your README? |
| 4 | **Code quality** | 10% | Clear structure, separation of concerns, honest error handling, no large blocks of dead code. |
| 5 | **Documentation & report** | 10% | Can someone else set this up and understand why it looks the way it does? Is the report specific and honest about what is missing? |
| 6 | **Resource efficiency** | 10% | Measured footprint, sensible polling design, lean frontend. Evidence beats claims. |
| 7 | **UX** | 5% | Clear under real conditions loading, empty, and error states included. |

**Bonus (up to +10%)** — anything genuinely useful we did not ask for like tests, CI, snapshots, alerting, metric export, a systemd unit, a thoughtful threat model.

**What will sink an otherwise working submission,**

- **Code you cannot explain.** This one is worth stating plainly: we expect you to use AI tools, and we will ask you why a given function is written the way it is, what you tried first, and what you would change. A working project whose author cannot defend its design scores below a smaller project whose author can.

We would rather see **fewer things done properly** than everything half-working.

---

## 🔗 Starter Repository

No starter code is provided, and no layout is prescribed. How you organize the repository — how backend concerns are separated, where the collector lives, how the frontend sits next to it — is one of the things we read, so we are not going to hand you a tree to fill in.

What has to be present, at whatever path makes sense to you:

- `README.md` and `REPORT.md`
- `.env.example`, with `.env` git-ignored
- Dependency manifests for both halves of the project
- A way to take an empty machine to an initialized database without manual SQL

**Submission:** share the repository with `dev@roboticgen.co` and reply to the task email with the repo link and the demo recording link before the hard deadline.

---

## ❓ Clarifications & Assumptions

Where the spec is silent, **make a reasonable decision and document it in the report.** Do not block waiting for an answer. If something is genuinely ambiguous and blocking, email dev@roboticgen.co — expect a reply within one working day.

---

## 📅 Deadlines

| Type | Date |
| --- | --- |
| Soft Deadline | 2026-08-13 |
| Hard Deadline | 2026-08-15 |

The soft deadline is the target. Submitting between the two costs nothing on its own, but time management is visible to reviewers. Nothing is accepted after the hard deadline.

---

## 📬 Contact

For technical clarifications,

📧 **dev@roboticgen.co**

---

## References

- Astro — https://astro.build/
- Falcon — https://falcon.readthedocs.io/en/stable/
- pylxd — https://pylxd.readthedocs.io/en/latest/
- TinyFlux — https://tinyflux.readthedocs.io/en/latest/index.html
- SQLite3 — https://docs.python.org/3/library/sqlite3.html
- LXD — https://canonical.com/lxd
- Google OAuth 2.0 — https://developers.google.com/identity/protocols/oauth2
