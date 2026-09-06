"""Shared helpers for the reaped-whirlwind services.

Bind-mounted read-only into each container at /srv/reaped/common (see
docker-compose.yml, which puts /srv/reaped on sys.path — a dedicated parent so
no other top-level container dir becomes importable);
importable as the `common` package from the repo root when running locally.
Each service bootstraps the import path with a two-line probe — see
`services/inference/inference_service.py` for the canonical form.

Keep this package dependency-light: `status` and `jsonlog` are stdlib-only and
`nws` needs only `requests`, so every service image can import it without a
rebuild.
"""
