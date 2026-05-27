# Deploy & Operations

The stack runs on the **kappa** Synology NAS (DSM 7.2.x) at `/volume1/docker/reaped-whirlwind/`,
as one compose project named `reaped-whirlwind`.

| Thing | Value |
|---|---|
| Host | kappa (`192.168.1.248`) |
| Project dir | `/volume1/docker/reaped-whirlwind` |
| Compose project | `reaped-whirlwind` |
| Ports | processor 9005 · weather 9006 · dashboard 9007 · inference 9008 · alerting 9009 |
| Containers | screenshot-service · radar-image-processor · weather-reporter · pipeline-dashboard · inference-service · alerting-service |

## First-time setup
```bash
cd /volume1/docker/reaped-whirlwind
cp .env.example .env        # set NWS_UA contact + SMTP creds
docker-compose up -d --build
```
`.env` holds secrets (SMTP) and is **gitignored** — recreate it from `.env.example` on a fresh
checkout. The data dirs under `/volume1/docker/...` are bind-mounted and must already exist.

## Restart vs rebuild  (important)
Each service's main code + config is **bind-mounted**, so:
- **Backend code / config / studio-list change** → `docker-compose restart <service>` (no rebuild,
  **same container** — does not churn the container ID).
- **Frontend / Dockerfile / dependency change** → `docker-compose up -d --build <service>` (rebuilds
  the image, **recreates** the container).

Prefer `restart`. `up -d --build` mints a new container ID, which Synology **Container Manager**
surfaces as a stale `<oldid>_<name>` ghost ("doesn't exist, but running") until the Container Manager
package is restarted. A `restart` avoids that entirely.

## Deploying a change (from the Mac, via the mage-hands relay)
```bash
# 1. commit + push
git push
# 2. ship the tracked tree to the NAS (scp/SFTP is DISABLED on kappa — use ssh cat-pipe).
#    Push .env separately if it has changed (gitignored, so NOT in the archive):
git archive --format=tar.gz HEAD | ssh magehands@192.168.1.248 'cat > /tmp/rw.tgz'
ssh magehands@192.168.1.248 'umask 077; cat > /tmp/rw.env' < .env   # only if .env changed
# 3. via the relay (root) — extract over the project dir, install .env, fix owner. Verify md5s
#    on both sides first, and back up the live docker-compose.yml + .env in case of rollback:
#    ts=$(date +%Y%m%d-%H%M%S)
#    cp -p docker-compose.yml docker-compose.yml.bak.$ts && cp -p .env .env.bak.$ts
#    tar xzf /tmp/rw.tgz -C /volume1/docker/reaped-whirlwind/
#    install -m 600 -o 1026 -g 100 /tmp/rw.env /volume1/docker/reaped-whirlwind/.env
#    chown -R 1026:100 /volume1/docker/reaped-whirlwind/{services,ml,models,data-tools,docs,...}
#    rm -f /tmp/rw.tgz /tmp/rw.env
# 4. apply: restart (code/config) or up -d --build (image/deps change).
#    For long builds, see "Relay 300s timeout" below — kick off via nohup and poll.
```

`magehands` now has docker access (added to the `docker` group; `/usr/bin/docker` +
`/usr/bin/docker-compose` symlinks resolve via the default ssh PATH). So
`ssh magehands@192.168.1.248 'docker ps'` / `docker logs <name>` work non-interactively — the relay
is still the right tool for root-required work (chown, file installs), but plain ssh is fine for
container introspection and `restart`.

## Synology gotchas (hard-won — do not relearn these in an outage)
- **The mage-hands relay container is `restart=no`.** Restarting Docker or the Container Manager
  package stops the relay and it will **not** come back on its own — you lose remote access
  mid-command. **Never bounce Docker / Container Manager through the relay.** Do it from the DSM UI;
  afterward bring the relay back with `~/.config/mage-hands/relay.sh kappa up`.
- **CM ghost after a CLI recreate.** After `up -d --build` (or the unify cut-over), Container Manager
  may show a `<oldid>_<name>` entry that errors on click. The real container is fine. Clear it by
  restarting the Container Manager package **only after** verifying the new stack is healthy.
- **Container Manager can't set `privileged`/some options** — manage this stack via CLI compose, not
  the CM GUI, or CM and the CLI will fight over it.
- **Relay 300s timeout.** `mcp__kappa__run` (the Claude MCP server backed by the relay) hard-caps
  each command at 300 s. A `docker-compose up -d --build inference alerting` (torch CPU wheel pull,
  ~5–10 min on first build) WILL appear to time out from Claude's side while still running on
  kappa. Pattern that works:
  ```sh
  nohup sh -c 'docker-compose -p reaped-whirlwind up -d --build inference alerting; \
               echo exit=$? > /tmp/rw-build.done' > /tmp/rw-build.log 2>&1 < /dev/null &
  ```
  Then poll `tail /tmp/rw-build.log` + `mcp__kappa__list_containers` until the new containers show
  `Status: Up X seconds (healthy)`. Do NOT retry the `up -d --build` from the start — it'll race
  the original.

## Verify
```bash
docker ps --filter label=com.docker.compose.project=reaped-whirlwind   # 6 services Up
curl -s localhost:9005/health                                          # processor
curl -s -o /dev/null -w '%{http_code}\n' localhost:9006                # weather
curl -s -o /dev/null -w '%{http_code}\n' localhost:9007                # dashboard
curl -s localhost:9008/health | jq '.status, .last_score'              # inference
curl -s localhost:9009/health | jq '.status, .active_warnings, .allowed_events'  # alerting
ls -t /volume1/docker/processed-weather-screenshots | head             # fresh JSON flowing
```

## Troubleshooting
| Symptom | Check |
|---|---|
| A service down | `docker logs --tail 50 <container>`; dashboard at :9007 |
| No new screenshots/JSON | processor `/health` (9005); screenshot logs; disk space |
| Weather empty / 403 | `NWS_UA` must be a real contact (NWS requirement) |
| Dashboard can't control a service | it needs `docker.sock` + the repo mounted at `/project`; rebuild uses `-p reaped-whirlwind` |
| Change didn't take | code/config = `restart`; image/deps = `--build` |
| CM shows `<id>_name` ghost | stale CM record after recreate — restart Container Manager package |
