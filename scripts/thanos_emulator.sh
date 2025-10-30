#!/usr/bin/env bash
# Thanos grader emulator for Part A (HTTP-only)
# - Tests baseline, chaos failover, stability window
# - Verifies headers forwarded unchanged
# - No Docker commands; targets host: 8080/8081/8082
# Usage:
#   bash scripts/thanos_emulator.sh              # default HOST=localhost
#   HOST=203.0.113.10 bash scripts/thanos_emulator.sh

set -euo pipefail

HOST="${HOST:-localhost}"
GATEWAY="http://${HOST}:8080"
BLUE="http://${HOST}:8081"
GREEN="http://${HOST}:8082"

# Tunables (can be overridden via env):
#   CURL_MAX_TIME      total per-request ceiling
#   CONNECT_TIMEOUT    TCP connect timeout
#   RETRIES            curl retry attempts for transient errors

CURL_MAX_TIME=${CURL_MAX_TIME:-5}   # per-request cap (<=10s constraint)
CONNECT_TIMEOUT=${CONNECT_TIMEOUT:-3}
RETRIES=${RETRIES:-3}
BASELINE_N=${BASELINE_N:-20}
FAILOVER_N=${FAILOVER_N:-50}
REQUIRED_GREEN_PCT=${REQUIRED_GREEN_PCT:-95}

# Force IPv4 by default to avoid IPv6 resolution/connectivity issues on some hosts.
# You can override by exporting CURL_FLAGS (e.g., "-6 -sS").
CURL_FLAGS=${CURL_FLAGS:-"-4 -sS"}

fail() { echo "[FAIL] $*" >&2; echo "GRADE: FAIL"; exit 1; }
log()  { echo "[INFO] $*"; }
pass() { echo "[PASS] $*"; }

probe() {
  # probe <url>
  local url="$1"
  if ! curl ${CURL_FLAGS} --connect-timeout "$CONNECT_TIMEOUT" -m "$CURL_MAX_TIME" --retry "$RETRIES" --retry-all-errors "$url" >/dev/null; then
    return 1
  fi
}

curl_headers() {
  local url="$1"
  # -sS silent but show errors; -m caps total time; -D - prints headers; discard body
  curl ${CURL_FLAGS} --connect-timeout "$CONNECT_TIMEOUT" -m "$CURL_MAX_TIME" --retry "$RETRIES" --retry-all-errors -D - "$url" -o /dev/null
}

status_code() { awk 'NR==1{print $2}'; }
header_val() { awk -v key="$1" 'BEGIN{IGNORECASE=1}{
  if(tolower($0)~"^" tolower(key) ":"){sub(/^[^:]*: */,"",$0); gsub(/\r$/,""); print $0}
}'; }

require_200() {
  local h="$1"
  local code
  code=$(printf "%s" "$h" | status_code)
  [[ "$code" == "200" ]] || fail "Expected HTTP 200, got $code"
}

# Preflight: probe gateway first, then apps. If app ports are blocked (common on cloud hosts),
# provide a clear message with workarounds.
log "Probing endpoints on ${HOST} (IPv4 forced by default; set CURL_FLAGS to override)..."
log "Using timeouts: CONNECT_TIMEOUT=${CONNECT_TIMEOUT}s, MAX_TIME=${CURL_MAX_TIME}s, RETRIES=${RETRIES}"

if ! probe "$GATEWAY/version"; then
  # One last, slower attempt before failing (still <10s)
  if ! curl ${CURL_FLAGS} --connect-timeout 5 -m 8 -sS "$GATEWAY/version" >/dev/null; then
    fail "Endpoint not reachable: $GATEWAY/version (check firewall, security group, or service status)"
  fi
fi
pass "Gateway reachable: $GATEWAY/version"

blue_ok=true; green_ok=true
probe "$BLUE/version" || blue_ok=false
probe "$GREEN/version" || green_ok=false

if [[ "$blue_ok" != true || "$green_ok" != true ]]; then
  log "Direct app ports check: blue=$blue_ok (8081), green=$green_ok (8082)"
  echo ""
  echo "[NOTE] Direct app ports 8081/8082 are not reachable from your machine."
  echo "       That's expected on many cloud servers due to firewall rules."
  echo "       The emulator needs these ports to start chaos and read ground-truth headers."
  echo ""
  echo "Workarounds:"
  echo "  - Run this emulator on the server (SSH in) and use HOST=localhost"
  echo "  - Or create an SSH tunnel locally, then run with HOST=localhost:"
  echo "      ssh -N -L 8081:localhost:8081 -L 8082:localhost:8082 <user>@${HOST}"
  echo "  - Or temporarily open inbound 8081 and 8082 to your IP for testing"
  echo ""
  fail "Cannot proceed without access to 8081/8082"
fi
pass "Direct app ports reachable"

# Discover direct app headers (ground truth for release IDs)
log "Reading direct headers (ground truth)..."
blue_h=$(curl_headers "$BLUE/version"); require_200 "$blue_h"
blue_pool=$(printf "%s" "$blue_h" | header_val 'X-App-Pool')
blue_rel=$(printf "%s" "$blue_h" | header_val 'X-Release-Id')
[[ "$blue_pool" == "blue" ]] || fail "Direct blue X-App-Pool should be 'blue', got '$blue_pool'"

green_h=$(curl_headers "$GREEN/version"); require_200 "$green_h"
green_pool=$(printf "%s" "$green_h" | header_val 'X-App-Pool')
green_rel=$(printf "%s" "$green_h" | header_val 'X-Release-Id')
[[ "$green_pool" == "green" ]] || fail "Direct green X-App-Pool should be 'green', got '$green_pool'"
pass "Direct headers OK (X-App-Pool and X-Release-Id discovered)"

# Baseline (Blue active): all 200, all from blue, release id matches blue_rel
log "Baseline: ${BASELINE_N} requests to $GATEWAY/version expecting blue..."
ok=0; blue_ok=0
for i in $(seq 1 "$BASELINE_N"); do
  h=$(curl_headers "$GATEWAY/version") || true
  code=$(printf "%s" "$h" | status_code)
  pool=$(printf "%s" "$h" | header_val 'X-App-Pool')
  rel=$(printf "%s" "$h" | header_val 'X-Release-Id')
  [[ "$code" == "200" ]] && ok=$((ok+1))
  if [[ "$pool" == "blue" && "$rel" == "$blue_rel" ]]; then
    blue_ok=$((blue_ok+1))
  else
    fail "Baseline req $i: expected X-App-Pool=blue and X-Release-Id=$blue_rel, got pool='$pool' rel='$rel'"
  fi
  sleep 0.1
done
[[ "$ok" -eq "$BASELINE_N" ]] || fail "Baseline: non-200s detected ($ok/$BASELINE_N were 200)"
pass "Baseline OK: $ok/$BASELINE_N 200s; all indicate blue ($blue_rel)"

# Induce chaos on Blue
log "Starting chaos on Blue (mode=error)..."
ch=$(curl ${CURL_FLAGS} -m "$CURL_MAX_TIME" -X POST "$BLUE/chaos/start?mode=error" || true)
[[ -n "$ch" ]] || fail "Chaos start on Blue did not respond"

# Immediate switch: next request should be 200 from green with green_rel
log "Verifying immediate switch to green..."
im=$(curl_headers "$GATEWAY/version"); require_200 "$im"
im_pool=$(printf "%s" "$im" | header_val 'X-App-Pool')
im_rel=$(printf "%s" "$im" | header_val 'X-Release-Id')
[[ "$im_pool" == "green" ]] || fail "Immediate request expected X-App-Pool=green, got '$im_pool'"
[[ "$im_rel" == "$green_rel" ]] || fail "Immediate request expected X-Release-Id=$green_rel, got '$im_rel'"

# Stability window (~10s): 0 non-200s, >=95% green
log "Stability window: ${FAILOVER_N} requests over ~10s..."
ok=0; green_count=0
for i in $(seq 1 "$FAILOVER_N"); do
  h=$(curl_headers "$GATEWAY/version") || true
  code=$(printf "%s" "$h" | status_code)
  pool=$(printf "%s" "$h" | header_val 'X-App-Pool')
  [[ "$code" == "200" ]] && ok=$((ok+1))
  [[ "$pool" == "green" ]] && green_count=$((green_count+1))
  sleep 0.2
done

[[ "$ok" -eq "$FAILOVER_N" ]] || fail "0 non-200s required during failure window ($ok/$FAILOVER_N)"
green_pct=$(( green_count * 100 / FAILOVER_N ))
log "Green percentage: ${green_pct}% (${green_count}/${FAILOVER_N})"
(( green_count * 100 >= REQUIRED_GREEN_PCT * FAILOVER_N )) || fail ">=${REQUIRED_GREEN_PCT}% responses must be from green"

# Cleanup chaos (best-effort)
curl ${CURL_FLAGS} -m "$CURL_MAX_TIME" -X POST "$BLUE/chaos/stop" >/dev/null || true

pass "All checks passed (Baseline, Immediate Switch, Stability, Headers)."
echo "GRADE: PASS"

# --- Stage 3 checks: observability & alerts ---
log "Running Stage-3 checks: structured logs and watcher alerts (outbox mode)"

# helper: wait for a file to contain a pattern (simple timeout loop)
wait_for_file_contains() {
  # $1=file $2=pattern $3=timeout
  local file="$1" pattern="$2" timeout=${3:-10}
  local start=$(date +%s)
  while true; do
    if [[ -f "$file" ]]; then
      if grep -q -E "$pattern" "$file"; then
        return 0
      fi
    fi
    now=$(date +%s)
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 1
  done
}

# 1) check Nginx structured logs contain required JSON fields
LOG_FILE="$(pwd)/logs/access.log"
log "Checking structured nginx logs at $LOG_FILE"
if [[ ! -f "$LOG_FILE" ]]; then
  fail "Nginx access log not found: $LOG_FILE"
fi

# Look for a recent JSON log line containing pool, release, upstream_status and upstream_addr
if tail -n 200 "$LOG_FILE" | grep -E '"pool"|"release"|"upstream_status"|"upstream_addr"' >/dev/null; then
  pass "Structured nginx log fields appear present"
else
  fail "Structured nginx log does not contain expected fields (pool/release/upstream_status/upstream_addr)"
fi

# 2) Wait for watcher outbox to receive a failover alert (created when failover occurred above)
OUTBOX="$(pwd)/watcher/outbox.log"
log "Waiting up to 15s for a failover alert in $OUTBOX (outbox mode)"
if wait_for_file_contains "$OUTBOX" "\tfailover\t" 15; then
  pass "Failover alert found in watcher outbox"
else
  fail "Failover alert not found in watcher outbox within timeout"
fi

# 3) Generate an error-rate alert: enable error mode on both apps and flood requests so watcher sees > threshold
log "Starting error-rate simulation: enabling error mode on both apps and sending many requests"
curl ${CURL_FLAGS} -m "$CURL_MAX_TIME" -X POST "$BLUE/chaos/start?mode=error" >/dev/null || true
curl ${CURL_FLAGS} -m "$CURL_MAX_TIME" -X POST "$GREEN/chaos/start?mode=error" >/dev/null || true

FLOOD_N=${FLOOD_N:-250}
for i in $(seq 1 "$FLOOD_N"); do
  curl ${CURL_FLAGS} --connect-timeout "$CONNECT_TIMEOUT" -m "$CURL_MAX_TIME" --retry "$RETRIES" --retry-all-errors -sS -o /dev/null "$GATEWAY/version" || true
  sleep 0.04
done

log "Waiting up to 20s for an error-rate alert in $OUTBOX"
if wait_for_file_contains "$OUTBOX" "\terror_rate\t" 20; then
  pass "Error-rate alert found in watcher outbox"
else
  fail "Error-rate alert not found in watcher outbox within timeout"
fi

# Cleanup chaos (best-effort)
curl ${CURL_FLAGS} -m "$CURL_MAX_TIME" -X POST "$BLUE/chaos/stop" >/dev/null || true
curl ${CURL_FLAGS} -m "$CURL_MAX_TIME" -X POST "$GREEN/chaos/stop" >/dev/null || true

pass "Stage-3 checks passed (structured logs + outbox alerts)"
echo "GRADE: PASS"
