# Runbook – Observability & Alerts for Blue/Green (Stage 3)

This runbook explains the alerts produced by the watcher sidecar and the recommended operator actions.

## Alert types

1) Failover detected (Blue → Green or Green → Blue)
- Meaning: Nginx switched the active upstream pool. Usually occurs after the primary fails a request or times out.
- Immediate checks:
  - docker ps
  - docker logs <primary-container> (app_blue or app_green)
  - curl http://localhost:8081/healthz (or 8082 for green)
  - Check Nginx access logs (`./logs/access.log`) for sample lines attached to the alert
- Actions:
  - If primary is unhealthy: inspect its logs and restart the container
    ```bash
    docker compose restart app_blue
    # or
    docker compose restart app_green
    ```
  - If the app is misbehaving after a recent deploy, consider toggling `ACTIVE_POOL` back or rolling back the deploy.
- Suppress alerts while performing maintenance: set `MAINTENANCE_MODE=1` in `.env` and restart the watcher.

2) High error-rate detected
- Meaning: Elevated proportion of 5xx responses over the configured window (e.g., >2% over last 200 requests).
- Immediate checks:
  - Inspect top upstreams reported in the alert
  - Check app container logs for exceptions or resource exhaustion
  - Verify recent deployments / release IDs
- Actions:
  - If a specific release is causing issues, remove it from rotation or roll back
  - If resource exhaustion, scale or restart the container

3) Recovery notifications
- The watcher will send a new failover alert when the active pool changes again; treat that as signal that the primary recovered and regained traffic.

## How to silence alerts during planned tests
- Set `MAINTENANCE_MODE=1` in `.env` and restart the watcher process:
  ```bash
  # edit .env, then
  docker compose restart alert_watcher
  ```
- Alternatively, temporarily set `ALERT_COOLDOWN_SEC` to a large value.

## Where to find logs and alerts
- Nginx access logs: `./logs/access.log` (JSON lines)
- Watcher outbox (local-only): `./watcher/outbox.log`
- Slack channel: configured via `SLACK_WEBHOOK_URL` in `.env`

## Troubleshooting
- Watcher not sending alerts:
  - Confirm `SLACK_WEBHOOK_URL` is set and reachable
  - Check watcher logs: `docker compose logs -f alert_watcher`
  - If using local outbox, check `./watcher/outbox.log`

- No logs in `./logs`:
  - Ensure nginx service has the volume mounted and `access_log` is configured
  - Confirm Nginx created the file inside container: `docker compose exec nginx ls -l /var/log/nginx`

## Contact
- On-call: Dev Team Slack channel
- Repo: https://github.com/username/repo (replace with your repo)
