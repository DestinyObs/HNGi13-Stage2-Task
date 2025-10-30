# Runbook — Observability & Alerts (Stage 3)

This runbook explains the alerts produced by the watcher, how the watcher determines errors, and exact reproducible command blocks graders/operators can use to verify alerts.

## Purpose

Use this document to:

- Understand what a Failover and High Error Rate alert contains.
- Reproduce the alerts deterministically for grading or triage.
- Learn where alerts are written if Slack is not configured.

## Key observability fields

The watcher relies on nginx producing structured JSON access logs. Each access log line must include these fields (the nginx template in this repo already provides them):

- `pool` — active pool header (blue or green)
- `release` — release identifier from upstream (X-Release-ID)
- `upstream_status` — comma-separated upstream HTTP codes nginx saw
- `upstream_addr` — comma-separated upstream addresses contacted by nginx
- `request_time` — total request time
- `upstream_response_time` — upstream response times (may be comma-separated)

Error definition (important): the watcher treats an entry as an "error" when either:

- the client-visible `status` is 5xx, OR
- any value in `upstream_status` is 5xx (this detects upstream failures that nginx may retry before returning 200 to the client).

## Alert types and contents

1. Failover detected
- Trigger: watcher sees the `pool` value in logs change (e.g., blue → green).
- Contents: window size, error count & percentage, sample log line, `release`, `upstream_status`, `upstream_addr`.

2. High error rate (`error_rate`)
- Trigger: percent of errors in the last `WINDOW_SIZE` requests >= `ERROR_RATE_THRESHOLD`.
- Contents: window size, error count & percentage, top upstream addresses (by error count), sample line.

## Getting started — bring the stack up (copy/paste)

Run these steps on the server or in the workspace where the repository lives. They cover creating a local `.env`, building images (if needed), starting the Compose stack, and verifying containers are running.

Prerequisites:
- Docker & Docker Compose installed on the host.
- A copy of this repository on the host (git clone or uploaded files).
- A local `.env` file in the repo root (do NOT commit your real webhook). See the `Security` section below.

Quick start commands (bash):

```bash
# 1) create .env from example (if present) and edit the values
cp .env.example .env 2>/dev/null || true
# Edit .env and set SLACK_WEBHOOK_URL and any overrides (WINDOW_SIZE, ERROR_RATE_THRESHOLD, ACTIVE_POOL)
${EDITOR:-nano} .env

# 2) start the stack
docker compose up -d

# 3) verify containers are healthy and running
docker compose ps
docker compose logs --no-color --tail 50 nginx
docker compose logs --no-color --tail 50 alert_watcher
```

Notes:
- If you make changes to the watcher code (`watcher/`) you can rebuild the watcher image specifically with `docker compose build alert_watcher` and then `docker compose up -d --no-deps --force-recreate alert_watcher` to pick up code changes.
- The `nginx` service writes its access log to the `logs/` folder (mounted into the `alert_watcher` container); the watcher tails `/var/log/nginx/access.log` inside the container using that mount.


## Verification: Failover alert (copy/paste)

Run these commands on the server (bash). They recreate the watcher, flip the active pool via the chaos endpoint, exercise the gateway and then show the single latest failover alert (clean for a screenshot).

```bash
# Recreate watcher (ensure the server .env has SLACK_WEBHOOK_URL set if you want Slack messages)
docker compose up -d --no-deps --force-recreate alert_watcher
sleep 2

# Activate chaos (makes primary return 5xx so nginx will failover)
curl -sS -X POST "http://localhost:8081/chaos/start?mode=error"

# Exercise the gateway once (this triggers log lines and the failover)
curl -i http://localhost:8080/version
sleep 5

# Stop chaos
curl -sS -X POST "http://localhost:8081/chaos/stop"

# Show the single latest failover alert (outbox fallback) for a clean screenshot
grep -i 'failover' watcher/outbox.log | tail -n 1

# Also collect watcher logs
docker compose logs --no-color --tail 120 alert_watcher
```

Notes:

- If `SLACK_WEBHOOK_URL` is configured and reachable you'll receive a Slack message. If you removed the webhook for security, the `outbox.log` line is the safe artifact.

## Verification: High error-rate alert (copy/paste)

This reproduces a noisy test while the backend is in error mode so upstream 5xx are recorded and the watcher computes a high error rate.

```bash
# Recreate watcher
docker compose up -d --no-deps --force-recreate alert_watcher
sleep 2

# Enable chaos to force upstream errors
curl -sS -X POST "http://localhost:8081/chaos/start?mode=error"

# Hammer the gateway; adjust count/delay as needed to cross the threshold
for i in $(seq 1 500); do
  curl -sS -o /dev/null -w "%{http_code}" http://localhost:8080/version
  printf " "
  sleep 0.01
done
echo

# Allow time for watcher to compute and send
sleep 5

# Stop chaos
curl -sS -X POST "http://localhost:8081/chaos/stop"

# Show the single latest error_rate alert (outbox)
grep -i 'error_rate' watcher/outbox.log | tail -n 1

# And watcher logs
docker compose logs --no-color --tail 120 alert_watcher
```

Tip: If you see many "Skipping alert ... in cooldown" messages, either increase the hammer iterations or temporarily lower `ALERT_COOLDOWN_SEC` in `.env` during grading.

## Inspecting logs & outbox

- Tail nginx access log:
  - `tail -F logs/access.log` or `docker compose logs -f nginx`
- Tail watcher logs:
  - `docker compose logs -f alert_watcher`
- Inspect outbox on the host:
  - `cat watcher/outbox.log | tail -n 100`

## Maintenance & silencing (temporary)

- Silence alerts during planned work by setting `MAINTENANCE_MODE=1` in the server `.env` and restarting the watcher:

```bash
# on the server where .env is located
# edit .env and set MAINTENANCE_MODE=1
docker compose up -d --no-deps --force-recreate alert_watcher
```

- Re-enable alerts by setting `MAINTENANCE_MODE=0` and restarting the watcher.

## Example expected Slack excerpt

Failover alert (short):

"*Failover detected: blue -> green*\\nFailover detected at 2025-10-30T20:23:06Z\\nWindow=11, errors=1 (9.09%)\\nRelease: green-1.0.0\\nUpstream status: 500, 200\\nUpstream addr: 172.18.0.2:3000, 172.18.0.3:3000\\nSample: {...}"

High error-rate alert (short):

"High error rate: 10.00% over last 10 requests\\nTop upstreams: [(172.18.0.3:3000, 9), (172.18.0.2:3000, 1)]\\nSample: {...}"

---
