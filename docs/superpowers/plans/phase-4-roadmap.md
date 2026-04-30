# Phase 4 Roadmap — CI/CD: GitHub Actions → ghcr.io → bare-VM pull

> Sketch, not an execution plan. Promote to a `/writing-plans` doc when we're ready to start.

## Goal

Push to `main` → GitHub Actions builds the extractor image → publishes to `ghcr.io/lychagin/openproject-extractor:<sha>` (and `:latest`) → production Ubuntu VM pulls and restarts the container without manual ssh / rebuild on the host.

Per project memory, production target is a bare Ubuntu VM, no Kubernetes. Single instance, file-based secrets, docker compose.

## Components

### 4.1 — Build & publish workflow

`.github/workflows/build-image.yml`:

- Triggers: push to `main`, manual `workflow_dispatch`.
- Build via `docker/build-push-action@v5`.
- Auth: `permissions: { contents: read, packages: write }` + login with `GITHUB_TOKEN` to ghcr.
- Tags: `:latest` + `:<short-sha>`. Optional: `:vN.M.K` from git tags.
- Multi-arch (amd64 + arm64) only if VM is arm — default skip.

Independent of the VM. Once this is merged, every push to main produces a fresh image. Worth doing first as a stand-alone step.

### 4.2 — Production compose override

`docker-compose.prod.yml`:

```yaml
services:
  extractor:
    build: !reset null     # turn off the dev build
    image: ghcr.io/lychagin/openproject-extractor:latest
    pull_policy: always    # pulls newest :latest each `docker compose up -d`
```

On the VM run `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` (or alias as `make prod-up`). On dev: nothing changes — `make up` still uses `build: .`.

### 4.3 — VM provisioning

The VM is bare Ubuntu. One-time setup:

- Install Docker engine + compose plugin (per `docs.docker.com/engine/install/ubuntu/`).
- Create `extractor` system user, group `docker`.
- `mkdir -p /srv/extractor && chown extractor:extractor /srv/extractor && chmod 750 /srv/extractor`.
- `git clone` the repo into `/srv/extractor` (or just copy the two compose files + `.env` + `.cert/`).
- Drop secrets: `/srv/extractor/.env` (mode 600, owner extractor), `/srv/extractor/.cert/bundle.pem` (mode 600).

Worth scripting with a small `scripts/provision-vm.sh`. Keep it idempotent so re-running doesn't break anything.

### 4.4 — Pull / deploy trigger

How does the VM know to pull a new image? Three options, ordered by complexity:

1. **Cron pull** (simplest). `*/5 * * * * cd /srv/extractor && docker compose -f docker-compose.yml -f docker-compose.prod.yml pull && docker compose ... up -d`. Latency ≤ 5 min after image push. No GH-side configuration needed.
2. **Watchtower container.** Drops in a sidecar that polls the registry and updates running containers. Less to maintain than cron.
3. **Webhook from GH.** GH Actions step calls an endpoint on the VM after pushing the image; the VM's listener triggers `docker compose pull && up -d`. Lowest latency but requires exposing a port on the VM (or tunnel via Tailscale / similar).

Recommendation: **start with cron**. Simple, no inbound ports, easy to reason about. Move to webhook only if 5-min staleness becomes annoying.

### 4.5 — Backup

Postgres data lives in named volume `pgdata`. Strategy options:

- Nightly `pg_dump` to a local file, rotated by `logrotate` (or just keep last 7).
- Same `pg_dump` to S3-compatible storage (Selectel / Yandex Object Storage). Owner of the VM picks.
- Filesystem-level: nightly `tar` of `/var/lib/docker/volumes/openprojectextractor_pgdata` — easier but works only if VM stops the container during backup.

Likely worth its own one-shot script run as cron from `extractor` user.

### 4.6 — Smoke check / observability

Currently the extractor has no HTTP surface. Two options:

- **Add `/healthz`** to extractor (small Flask/FastAPI route returning last successful cycle time). Then VM monit or Uptime Kuma can ping it.
- **No HTTP** — rely on `docker compose ps` + `docker compose logs --tail=50 extractor`. Fine for a pet ops, no public dashboard.

Don't bother adding the healthz endpoint until needed.

## Suggested execution order

1. **4.1** — Workflow file. Validates the registry path before VM is involved. Stand-alone PR.
2. **4.2** — Production override compose. Test by running `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` locally — should pull from ghcr instead of building.
3. **4.3** — Spin up the VM, manual provisioning, manual `compose up`. Verify the pipeline once end-to-end.
4. **4.4 (cron)** — Drop in the cron, test by pushing a no-op commit and watching it land within 5 min.
5. **4.5** — Backup script. Don't skip — running 24/7 without backup is the moment you regret most.
6. **4.6** — Only if/when monitoring becomes a real pain point.

## Open questions to settle before starting

- **Image visibility.** Public on ghcr.io (anyone can pull) or private (VM uses a personal access token to authenticate)? Public is simpler. Whether the project is sensitive depends on what stakeholders expect.
- **Tag strategy.** `:latest` is convenient but pins production to whatever was last built — including broken commits. Alternative: VM pins `:v1.2.3` or `:<sha>`, and a separate "promote" step (manual or via tag) bumps prod. Worth doing a few `:latest` deploys first to feel the pain before adding ceremony.
- **Where do secrets really live in prod?** Local `.env` is the default. If the VM has multiple users or third-party access, escalate to `docker-compose secrets:` (file mounts) or a secret manager (Vault / Yandex Lockbox / sops). Per memory, user prefers to stay simple — start with `.env`.
