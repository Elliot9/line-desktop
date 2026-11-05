#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import time

output = Path(os.environ['LINE_SMOKE_OUTPUT'])
results = output / 'results.json'
if results.exists():
    results.unlink()
with (output / 'gtk.log').open('w') as log:
    process = subprocess.Popen(['./line-app', '--demo', '--smoke-test', str(output)], stdout=log, stderr=log)
    deadline = time.monotonic() + 40
    captured = False
    while process.poll() is None and time.monotonic() < deadline:
        try:
            result = json.loads(results.read_text())
            if result.get('ready_for_screenshot') and not captured:
                time.sleep(0.7)
                subprocess.run(['import', '-window', 'root', str(output / 'desktop.png')], check=True, timeout=5)
                captured = True
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
        raise SystemExit('GTK smoke timed out')
    if process.returncode:
        raise SystemExit(f'GTK smoke failed; inspect {output / "gtk.log"}')
result = json.loads(results.read_text())
assert result.get('passed') and captured, result
assert len(result['checks']) >= 10, result
print(f'PASS {len(result["checks"])} native GTK checks; screenshot {output / "desktop.png"}')
