# Sprint 0 feasibility probes

These are isolated environment probes, not application implementations. Run in
Ubuntu using a Linux-filesystem virtual environment/build directory. See
../../docs/sprint0-evidence.md for actual observations and remaining gates.

## Python

From the repository root:

```bash
python3 -m venv ~/projects/hsm-sprint0/.venv
~/projects/hsm-sprint0/.venv/bin/python -m pip install -r scripts/sprint0/requirements.txt
~/projects/hsm-sprint0/.venv/bin/python -m pip check
~/projects/hsm-sprint0/.venv/bin/python scripts/sprint0/tinyflux_probe.py --containers 10 --hours 1
```

The TinyFlux probe alone opens its temporary CSV, checks persistence after close
and reopen, and reports Linux process peak RSS. It does not establish exclusive
ownership enforcement in the eventual collector. Defaults simulate a full day;
`--hours 1` tests the selected smaller partition. No real LXD data is used.

## LXD exec

After the `hsm-smoke-renamed` container exists and is running:

```bash
~/projects/hsm-sprint0/.venv/bin/python scripts/sprint0/lxd_exec_probe.py
```

The probe sends exactly 131072 bytes to a pylxd stdout handler and reports its
chunk count. It is coupled to the Sprint 0 disposable container name and must
not be treated as application code. Its output can show stream delivery, while
the later terminal controller still needs a separate deadline and LXD control
channel termination design. The pinned `Instance.execute` signature has no
timeout parameter.

## LXD inventory

```bash
~/projects/hsm-sprint0/.venv/bin/python scripts/sprint0/lxd_inventory_probe.py
```

The probe enumerates every local project and then each project’s instances
through pylxd. It demonstrates host-wide discovery for the local test setup;
the application still needs its own stable-ID mapping, persisted inventory, and
authorization before it can expose any of this data.

Dependency purposes and costs:

| Direct dependency | Purpose | Resource/security implications |
| --- | --- | --- |
| tinyflux | CSV time-series feasibility | In-memory indexes/read results; measured in this probe; requires one file owner |
| pylxd | Planned LXD integration/exec probe | Privileged daemon access; HTTP, crypto and websocket dependencies |
| falcon | Planned API runtime compatibility | Request-serving process; authentication is application responsibility |
| gunicorn | Planned Linux WSGI runtime | Master plus worker processes; do not omit master from later measurements |
| google-auth | Planned Google claim verification | Cryptographic and certificate dependencies; real OAuth still unverified |
| requests | Planned bounded HTTP/OAuth transport | CA bundle/HTTP parsing; timeouts must be supplied by application |

The remaining requirements entries pin resolved transitive dependencies for this
environment. Installing them and `pip check` do not prove API/OAuth/LXD behavior.
No stack substitution is selected. No application footprint claim is made.

## Astro

Linux Node 24.21.0 and npm 11.19.0 were used. The fixed PATH below applies only to
these probe commands and prevents accidental use of Windows npm.

```bash
export PATH="$HOME/.local/share/hsm-tools/node-v24.21.0-linux-x64/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin"
mkdir -p ~/projects/hsm-sprint0
# Copy once into a fresh probe directory; do not overwrite unrelated work.
cp -r scripts/sprint0/astro-smoke ~/projects/hsm-sprint0/
cd ~/projects/hsm-sprint0/astro-smoke
npm ci --no-audit --no-fund
ASTRO_TELEMETRY_DISABLED=1 npm run build
```

Astro 7.3.3 is the only direct npm dependency. It adds 187 installed packages in
this environment, for build time only; output is a static HTML file. The lockfile
pins resolution/integrity. npm warned that esbuild's install script lacks an
allowScripts entry; both tested builds nevertheless succeeded. No new script
approval was added. A build pass is not a vulnerability audit or browser workflow
pass. Inspect `dist/index.html` for the clearly labelled build-probe page.
