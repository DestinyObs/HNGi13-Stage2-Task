# Runbook — Observability & Alerts (Stage 3)

This runbook explains the alerts produced by the watcher and how to triage them.

Alert types
1. Failover detected (blue → green or green → blue)
   - Meaning: Nginx switched active upstream. Usually indicates primary failed.
   - Immediate checks:
     - `docker ps` — check app_blue/app_green health
     - `docker logs <container>` for the previously active container
     - `curl http://localhost:8081/healthz` (direct container ports)
   - Recovery actions:
     - Restart failing container: `docker restart <container>`
     - If container is healthy but issue persists, consider toggling ACTIVE_POOL and reloading nginx (only if safe).
   - Suppress alerts during planned maintenance: set `MAINTENANCE_MODE=1` in `.env` and restart watcher.

2. High error rate
   - Meaning: > ERROR_RATE_THRESHOLD percent of last WINDOW_SIZE requests were 5xx.
   - Immediate checks:
     - Inspect top upstreams included in alert.
     - Check app logs around the timestamp.
     - Check recent deployments / release ids in headers.
   - Recovery actions:
     - Roll back recent changes if error correlates with a release.
     - Investigate resource exhaustion (CPU/memory), restart container if appropriate.
   - Suppress: `MAINTENANCE_MODE=1`.

Maintenance and silencing
- To silence alerts during planned tests:
  - Set `MAINTENANCE_MODE=1` in `.env` and restart the watcher (or send SIGHUP if watcher supports reload).
  - Re-enable by setting `MAINTENANCE_MODE=0` and restarting watcher.

Where alerts are written
- If `SLACK_WEBHOOK_URL` is set, alerts are posted to Slack.
- If empty, alerts are written to the watcher outbox file (default `/watcher/outbox.log` inside the watcher container or `watcher/outbox.log` on the host if mounted).

Useful commands
- Tail nginx access log:
  - `tail -F logs/access.log` (or `docker compose logs -f nginx`)
- Tail watcher logs:
  - `docker compose logs -f alert_watcher`
- Inspect outbox on server:
  - `cat watcher/outbox.log | tail -n 100`

Security note
- Do not commit real credentials to the repo. Put `SLACK_WEBHOOK_URL` into a local `.env` (gitignored) on the server.
