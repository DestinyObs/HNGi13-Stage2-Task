# Blue/Green with Nginx Upstreams (Auto-Failover + Manual Toggle)

Lightweight blue/green demo using Nginx as a reverse proxy with request-level retries and auto-failover.

This repo contains:

- Two tiny Node.js app instances (blue, green) served by prebuilt images
- An Nginx gateway with automatic retry and failover configuration
- A Python `alert_watcher` that tails Nginx JSON access logs and sends alerts (Slack or local outbox)

This README gives a clear project overview, file map, and quickstart. For detailed observability and grading steps see `runbook.md`.

---

## Repository layout

- `docker-compose.yml` — service definitions: `nginx`, `app_blue`, `app_green`, `alert_watcher`
- `nginx/` — Nginx templates and helper scripts:
  - `default.conf.template` — JSON access log format and proxy config
  - `10-active-pool.envsh` / `render-and-reload.sh` — helpers to set `ACTIVE_POOL` and reload nginx
- `watcher/` — `watcher.py` (main logic), `requirements.txt`, `test_runner.py` (local tests)
- `scripts/` — helper scripts such as `verify.sh` and a tiny emulator
- `logs/` — mounted nginx logs (when Compose runs locally)
- `runbook.md` — full observability/runbook with copy/paste commands for grading

## Quick highlights

- Auto failover: Nginx retries a failing upstream in the same client request and marks it down quickly so the backup serves traffic.
- Manual toggle: change `ACTIVE_POOL` and run the render script to switch pools without rebuilding.
- Observability: Nginx writes structured JSON logs; `alert_watcher` tails them and posts Slack alerts or writes an `outbox.log` fallback.

## Prerequisites

- Docker and Docker Compose (Docker Desktop on Windows with WSL2 recommended)
- Ports: 8080 (gateway), 8081 (blue), 8082 (green)

## Configuration

Copy the example env and edit locally (do not commit secrets):

```bash
cp .env.example .env 2>/dev/null || true
${EDITOR:-nano} .env
```

Important env vars:

- `BLUE_IMAGE`, `GREEN_IMAGE` — upstream app images (defaults point to prebuilt images)
- `ACTIVE_POOL` — `blue` or `green` (initial active pool)
- `SLACK_WEBHOOK_URL` — optional; watcher posts to Slack when set, otherwise writes to `watcher/outbox.log`
- `WINDOW_SIZE`, `ERROR_RATE_THRESHOLD`, `ALERT_COOLDOWN_SEC` — watcher tuning

## Quickstart

```bash
# Start the stack
docker compose up -d

# Check services
docker compose ps
docker compose logs --no-color --tail 50 nginx
docker compose logs --no-color --tail 50 alert_watcher
```

## Verify the gateway

```bash
curl -i http://localhost:8080/version
```

You should see `X-App-Pool` and `X-Release-Id` headers indicating which upstream served the request.

## Trigger failover (chaos)

```bash
# Make the blue app return errors directly
curl -sS -X POST "http://localhost:8081/chaos/start?mode=error"
curl -i http://localhost:8080/version
curl -sS -X POST "http://localhost:8081/chaos/stop"
```

## Rebuilding watcher after edits

```bash
docker compose build alert_watcher
docker compose up -d --no-deps --force-recreate alert_watcher
```

## Observability & alerts (short)

- `nginx/default.conf.template` emits structured JSON access logs with fields `pool`, `release`, `upstream_status`, `upstream_addr`, etc.
- `watcher/watcher.py` tails the log and:
  - Detects pool flips (failovers)
  - Computes a sliding-window error rate (counts upstream 5xx too)
  - Posts alerts to Slack or writes to `watcher/outbox.log` when webhook isn't configured

For the grader-friendly verification steps and exact screenshot commands, see `runbook.md`.

## Troubleshooting

- Watcher cooldown messages: the watcher enforces `ALERT_COOLDOWN_SEC`. For grading you may temporarily reduce it in `.env`.
- No upstream status in logs: confirm a sample line with `tail -n 1 logs/access.log | jq .`.

## Cleanup

```bash
docker compose down -v
```

**DevTo Article:** [Building a Self-Healing Blue/Green Deployment with Nginx & Docker](https://dev.to/destinyobs/building-a-self-healing-bluegreen-deployment-with-nginx-docker-3k12)