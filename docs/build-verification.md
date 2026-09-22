# Frontend build evidence — 2026-09-22

Executed from `frontend` on Windows with Node v24.11.1 and npm 11.6.2.

1. `npm ping --fetch-retries=0 --fetch-timeout=15000`: PONG, 857 ms.
2. `npm ci --prefer-offline --no-audit --no-fund --fetch-retries=0 --fetch-timeout=30000`: 193 packages added in 1 minute, exit 0. No manifest/lockfile changes.
3. First `npm run build`: failed because `containers.astro` contained Windows-1252 punctuation rather than UTF-8.
4. Corrected that encoding and replaced the static-build-incompatible dynamic detail route with `/containers/detail?id=<stable-id>`.
5. `npm run build`: exit 0; Astro 7.3.3 generated six pages in 2.54 seconds:
   `/`, `/dashboard`, `/containers`, `/containers/detail`, `/users`, `/terminal`.

The previous ECONNRESET failures were real but no longer block this build.
A static production build does not prove Google login or authenticated browser workflows.
Build output and node_modules are ignored; package-lock.json is unchanged.
