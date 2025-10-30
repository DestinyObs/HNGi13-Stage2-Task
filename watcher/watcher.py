#!/usr/bin/env python3
"""
Simple log watcher that tails an nginx JSON access log, computes a sliding-window
error rate, detects pool failovers, and posts alerts to Slack via webhook.

Behavior:
- Reads env vars from environment (or docker-compose .env)
- If SLACK_WEBHOOK_URL is empty, writes alerts to ./watcher/outbox.log for local testing
"""

import os
import sys
import time
import json
import logging
from collections import deque, Counter
from datetime import datetime

try:
    import requests
except Exception:
    requests = None

# Configuration from env
LOG_PATH = os.environ.get('LOG_PATH', '/var/log/nginx/access.log')
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '').strip()
ERROR_RATE_THRESHOLD = float(os.environ.get('ERROR_RATE_THRESHOLD', '2'))
WINDOW_SIZE = int(os.environ.get('WINDOW_SIZE', '200'))
ALERT_COOLDOWN_SEC = int(os.environ.get('ALERT_COOLDOWN_SEC', '300'))
MAINTENANCE_MODE = os.environ.get('MAINTENANCE_MODE', '0') in ('1', 'true', 'True')
OUTBOX = os.environ.get('OUTBOX_PATH', '/watcher/outbox.log')

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('watcher')

# state
window = deque(maxlen=WINDOW_SIZE)  # each entry: (status:int, pool:str, raw_line:str, ts:float)
# initialize last_seen_pool from ACTIVE_POOL if provided to avoid false-positive
# alerts before any log line is seen
last_seen_pool = os.environ.get('ACTIVE_POOL')
if last_seen_pool == '':
    last_seen_pool = None
last_alert_ts = {}  # alert_type -> timestamp


def now_ts():
    return time.time()


def send_alert(alert_type, title, body):
    """Send alert to Slack or write locally if webhook not provided."""
    ts = now_ts()
    last = last_alert_ts.get(alert_type, 0)
    if ts - last < ALERT_COOLDOWN_SEC:
        logger.info('Skipping alert %s: in cooldown', alert_type)
        return False
    if MAINTENANCE_MODE:
        logger.info('Maintenance mode ON, skipping alert %s', alert_type)
        return False

    payload = {
        'text': f'*{title}*\n{body}'
    }
    if SLACK_WEBHOOK_URL and requests:
        try:
            r = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=5)
            if r.status_code >= 200 and r.status_code < 300:
                logger.info('Alert %s posted to Slack', alert_type)
            else:
                logger.warning('Slack returned status %s: %s', r.status_code, r.text)
                # persist to outbox so operators still see it when Slack is misconfigured
                _write_outbox(alert_type, payload)
        except Exception as e:
            logger.exception('Failed to post alert to Slack: %s', e)
            _write_outbox(alert_type, payload)
    else:
        _write_outbox(alert_type, payload)

    last_alert_ts[alert_type] = ts
    return True


def _write_outbox(alert_type, payload):
    try:
        os.makedirs(os.path.dirname(OUTBOX), exist_ok=True)
        with open(OUTBOX, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.utcnow().isoformat()}Z\t{alert_type}\t{json.dumps(payload)}\n")
        logger.info('Wrote alert to outbox (%s)', OUTBOX)
    except Exception:
        logger.exception('Failed to write outbox')


def parse_line(line):
    try:
        data = json.loads(line)
    except Exception:
        # fallback: try to extract status and uri with crude parsing
        logger.debug('Failed to parse JSON; attempting fallback parse: %s', line.strip())
        # Fallback: very small best-effort extraction of status and pool/upstream fields
        out = {}
        try:
            # status: look for "status":123 or "status": 123
            import re
            m = re.search(r'"status"\s*:\s*(\d{3})', line)
            if m:
                out['status'] = int(m.group(1))
            m = re.search(r'"pool"\s*:\s*"([^"]+)"', line)
            if m:
                out['pool'] = m.group(1)
            m = re.search(r'"release"\s*:\s*"([^"]+)"', line)
            if m:
                out['release'] = m.group(1)
            m = re.search(r'"upstream_addr"\s*:\s*"([^"]+)"', line)
            if m:
                out['upstream_addr'] = m.group(1)
        except Exception:
            logger.debug('Fallback regex parse failed')
        # If we couldn't parse anything useful, return None
        if not out:
            return None
        return out
    return data


def process_record(data, raw_line):
    global last_seen_pool
    status = int(data.get('status', 0))
    pool = data.get('pool')
    release = data.get('release')
    upstream_status = data.get('upstream_status')
    upstream_addr = data.get('upstream_addr')
    ts = now_ts()
    window.append((status, pool, raw_line, ts))

    # compute error rate
    total = len(window)
    errors = sum(1 for s, _, _, _ in window if s >= 500)
    error_rate = (errors / total * 100) if total > 0 else 0.0

    # pool flip detection
    if pool:
        if last_seen_pool is None:
            # first observation
            last_seen_pool = pool
        elif pool != last_seen_pool:
            title = f'Failover detected: {last_seen_pool} → {pool}'
            body_lines = [
                f'Failover detected at {datetime.utcnow().isoformat()}Z',
                f'Window={total}, errors={errors} ({error_rate:.2f}%)',
            ]
            if release:
                body_lines.append(f'Release: {release}')
            if upstream_status:
                body_lines.append(f'Upstream status: {upstream_status}')
            if upstream_addr:
                body_lines.append(f'Upstream addr: {upstream_addr}')
            body_lines.append(f'Sample: {raw_line.strip()}')
            body = '\n'.join(body_lines)
            # update last_seen_pool immediately to avoid repeated alerts for the same flip
            last_seen_pool = pool
            send_alert('failover', title, body)

    # error-rate alert
    if total >= 10 and error_rate > ERROR_RATE_THRESHOLD:
        title = f'High error rate: {error_rate:.2f}% over last {total} requests'
        # include top upstream addrs if present
        addrs = Counter()
        for _, _, line_text, _ in window:
            try:
                d = json.loads(line_text)
                a = d.get('upstream_addr') or 'unknown'
            except Exception:
                a = 'unknown'
            addrs[a] += 1
        top = addrs.most_common(3)
        body = f'{title}\nTop upstreams: {top}\nSample: {raw_line.strip()}'
        send_alert('error_rate', title, body)


def tail_file(path):
    # Open and tail the file. If file doesn't exist yet, retry non-recursively.
    logger.info('Tailing %s', path)
    while True:
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                fh.seek(0, os.SEEK_END)
                while True:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.5)
                        continue
                    data = parse_line(line)
                    if data is None:
                        continue
                    process_record(data, line)
        except FileNotFoundError:
            logger.warning('Log file not found: %s; retrying in 1s', path)
            time.sleep(1)
            continue
        except Exception:
            logger.exception('Error while tailing file; retrying in 1s')
            time.sleep(1)
            continue


def main():
    logger.info('Starting watcher; LOG_PATH=%s, WINDOW=%s, THRESH=%s%%, COOLDOWN=%ss', LOG_PATH, WINDOW_SIZE, ERROR_RATE_THRESHOLD, ALERT_COOLDOWN_SEC)
    tail_file(LOG_PATH)


if __name__ == '__main__':
    main()
