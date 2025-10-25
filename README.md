# Blue/Green with Nginx Upstreams (Auto-Failover + Manual Toggle)

Lightning-fast Blue/Green behind Nginx with request-level retries and zero-downtime failover—run one command, chaos-test, and watch it switch instantly.

This deploys two prebuilt Node.js services (Blue, Green) behind Nginx with:
- Blue as primary by default, Green as backup
- Auto failover on 5xx/timeout within the same client request (retry to backup)
- Manual toggle via `ACTIVE_POOL` without rebuilding images
- Header passthrough (X-App-Pool, X-Release-Id)

## Prerequisites
- Windows: Docker Desktop running
- Ports free: 8080 (Nginx), 8081 (Blue), 8082 (Green)

## Configure
1) Copy `.env.example` to `.env` and adjust values if needed:
```
BLUE_IMAGE=yimikaade/wonderful:devops-stage-two
GREEN_IMAGE=yimikaade/wonderful:devops-stage-two
ACTIVE_POOL=blue
RELEASE_ID_BLUE=blue-1.0.0
RELEASE_ID_GREEN=green-1.0.0
PORT=3000
```

## Run (WSL/bash)
```bash
# From the repo root
docker compose up -d
```

## Verify baseline (Blue active)
- GET via Nginx:
```bash
curl -i http://localhost:8080/version
```
Expected: 200 with headers `X-App-Pool: blue` and `X-Release-Id: <RELEASE_ID_BLUE>`.

## Induce downtime on Blue
Trigger chaos on Blue's direct port (8081):
```bash
curl -X POST "http://localhost:8081/chaos/start?mode=error"
```
Next request(s) to the gateway should return Green with no 5xx:
```bash
curl -i http://localhost:8080/version
```
To stop chaos:
```bash
curl -X POST "http://localhost:8081/chaos/stop"
```

## Manual toggle (without restart)
Switch active to Green:
```bash
docker compose exec nginx sh -lc "ACTIVE_POOL=green /opt/nginx/render-and-reload.sh"
```
Switch back to Blue:
```bash
docker compose exec nginx sh -lc "ACTIVE_POOL=blue /opt/nginx/render-and-reload.sh"
```

## Notes on reliability and timing
- Tight timeouts (connect 500ms, read/send 1s) + `proxy_next_upstream` on timeout/5xx ensure instant retry to backup within the same request.
- `max_fails=1` + `fail_timeout=3s` rapidly marks a failing primary as down so backup is used consistently during the outage.
- `proxy_next_upstream_tries=2` and `proxy_next_upstream_timeout=2s` keep total under 10s.


## One-shot full verification (optional)
```bash
bash scripts/verify.sh
```

## Cleanup
```bash
docker compose down -v
```
**DevTo Article:** [Building a Self-Healing Blue/Green Deployment with Nginx & Docker](https://dev.to/destinyobs/building-a-self-healing-bluegreen-deployment-with-nginx-docker-3k12)
