# Key decisions and rationale

- Upstreams and roles: Nginx upstream uses the `backup` role per requirement. The primary server has `max_fails=1` and `fail_timeout=3s` so a single 5xx/timeout triggers demotion quickly and the backup takes traffic.
- Request-level retry: `proxy_next_upstream error timeout http_500 http_502 http_503 http_504 non_idempotent`, with `proxy_next_upstream_tries=2` and `proxy_next_upstream_timeout=2s`, ensures Nginx retries to the backup within the same client request on timeout/5xx and stays under the 10s cap.
- Tight timeouts: `proxy_connect_timeout=500ms`, `proxy_send_timeout=1s`, `proxy_read_timeout=1s` accelerate detection and failover.
- Headers: We do not strip headers and explicitly pass through `X-App-Pool` and `X-Release-Id` via `proxy_pass_header` so clients see upstream headers unchanged.
- Template & toggle: `ACTIVE_POOL` decides which server has the `backup` flag. The official nginx image renders `/etc/nginx/templates/*.template` using envsubst and variables set by `/docker-entrypoint.d/10-active-pool.envsh`. A helper script (`render-and-reload.sh`) enables toggling without restarting the container.
- Constraints honored: Only Docker Compose, no image builds, no Kubernetes, Blue/Green exposed on 8081/8082 so chaos can be triggered directly by the grader.
