"""VeloTracker - BLE diagnostic (macOS).

Tests the BLE server alone (no camera). Simulates 75 RPM pedaling.

Run this, then open MyWhoosh on the same Mac:
  1. Go to Device Connection
  2. Tap Controllable
  3. Pair with 'VeloTracker'
  4. Should see ~9.5 km/h, ~75 RPM, ~90 W

Ctrl+C to stop.
"""

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.ble_server import BLECadenceServer

print("=" * 60)
print("  VeloTracker BLE Diagnostic (macOS)")
print("=" * 60)
print()
print("Simulating 75 RPM pedaling.")
print("Open MyWhoosh -> Device Connection -> Controllable -> pair with 'VeloTracker'.")
print("Expected: ~9.5 km/h, ~75 RPM, ~90 W")
print()
print("Ctrl+C to stop.")
print()

server = BLECadenceServer()
server.start()
time.sleep(2.0)
print(f"[Diag] Status: {server.status}")
print()

try:
    rev = 0
    while True:
        time.sleep(0.8)  # ~75 RPM
        rev += 1
        server.update(rev, current_rpm=75.0)
        print(f"[Diag] Rev: {rev} | RPM: 75.0 | Status: {server.status}")
except KeyboardInterrupt:
    print("\n[Diag] Stopping...")
finally:
    server.stop()
    print("[Diag] Done.")
