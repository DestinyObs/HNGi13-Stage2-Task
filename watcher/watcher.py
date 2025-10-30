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
        logger.debug('Failed to parse JSON: %s', line.strip())
        return None
    return data


def process_record(data, raw_line):
    global last_seen_pool
    status = int(data.get('status', 0))
    pool = data.get('pool')
    ts = now_ts()
    window.append((status, pool, raw_line, ts))

    # compute error rate
    total = len(window)
    errors = sum(1 for s, _, _, _ in window if s >= 500)
    error_rate = (errors / total * 100) if total > 0 else 0.0

    # pool flip detection
    if pool:
        if last_seen_pool is None:
            last_seen_pool = pool
        elif pool != last_seen_pool:
            title = f'Failover detected: {last_seen_pool} → {pool}'
            body = f'Failover detected at {datetime.utcnow().isoformat()}Z\nWindow={total}, errors={errors} ({error_rate:.2f}%)\nSample: {raw_line.strip()}'
            sent = send_alert('failover', title, body)
            if sent:
                last_seen_pool = pool

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
    # open and seek to end
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            fh.seek(0, os.SEEK_END)
            logger.info('Tailing %s', path)
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
        logger.error('Log file not found: %s', path)
        # keep retrying until file appears
        while True:
            time.sleep(1)
            if os.path.exists(path):
                return tail_file(path)


def main():
    logger.info('Starting watcher; LOG_PATH=%s, WINDOW=%s, THRESH=%s%%, COOLDOWN=%ss', LOG_PATH, WINDOW_SIZE, ERROR_RATE_THRESHOLD, ALERT_COOLDOWN_SEC)
    tail_file(LOG_PATH)


if __name__ == '__main__':
    main()
