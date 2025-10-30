#!/usr/bin/env python3
"""Small test runner that exercises parse/process logic in watcher.py
Writes alerts to watcher/outbox.log (no Slack webhook configured).
"""
import os, json, time, importlib.util
# Ensure outbox is local to repo watcher/outbox.log
OUTBOX = os.path.join(os.getcwd(), 'watcher', 'outbox.log')
os.environ['OUTBOX_PATH'] = OUTBOX
os.environ['SLACK_WEBHOOK_URL'] = ''
os.environ['WINDOW_SIZE'] = '10'
os.environ['ERROR_RATE_THRESHOLD'] = '20'  # 20% to make trigger easier
os.environ['ALERT_COOLDOWN_SEC'] = '1'
os.environ['MAINTENANCE_MODE'] = '0'
os.environ['ACTIVE_POOL'] = 'blue'

# Import watcher module by file path (safe regardless of package layout)
spec = importlib.util.spec_from_file_location('watcher_mod', os.path.join(os.getcwd(), 'watcher', 'watcher.py'))
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)

print('Initial last_seen_pool:', watcher.last_seen_pool)

# Prepare sample lines
blue = json.dumps({
    "time":"2025-10-30T00:00:00Z","remote_addr":"1.2.3.4","method":"GET","uri":"/version",
    "status":200,"pool":"blue","release":"blue-1.0.0","upstream_status":200,"upstream_addr":"172.17.0.2:3000","request_time":0.12,"upstream_response_time":"0.12"
})

green = json.dumps({
    "time":"2025-10-30T00:00:01Z","remote_addr":"1.2.3.4","method":"GET","uri":"/version",
    "status":200,"pool":"green","release":"green-1.0.0","upstream_status":200,"upstream_addr":"172.17.0.3:3000","request_time":0.11,"upstream_response_time":"0.11"
})

print('Parsing blue...')
d = watcher.parse_line(blue)
print('Parsed:', d.get('pool'), d.get('status'))
watcher.process_record(d, blue)

print('Simulating failover to green...')
d = watcher.parse_line(green)
watcher.process_record(d, green)
print('After processing, last_seen_pool in module:', watcher.last_seen_pool)

# Simulate multiple 500s to trigger error-rate alert
print('Generating errors to trigger error-rate alert...')
for i in range(6):
    err = json.dumps({"status":500, "pool":"green", "release":"green-1.0.0", "upstream_status":500, "upstream_addr":"172.17.0.3:3000"})
    watcher.process_record(watcher.parse_line(err), err)
    time.sleep(0.05)

print('Done. Check', OUTBOX, 'for outbox alerts (or watcher/outbox.log in container).')
