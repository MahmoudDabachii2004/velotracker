"""VeloTracker - BLE diagnostic (cross-platform).

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
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.ble_server import BLECadenceServer

print("=" * 60)
print("  VeloTracker BLE Diagnostic (Zigzag Mode)")
print("=" * 60)
print()
print("Simulating a dynamic RPM (50 -> 110 -> 50) to test latency.")
print("Open MyWhoosh and watch the values respond in real-time.")
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
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        # Cycle of 30 seconds
        cycle = (elapsed % 30.0) / 30.0
        if cycle < 0.5:
            # Ramping up from 50 to 110 RPM
            current_rpm = 50.0 + (cycle * 2.0) * 60.0
        else:
            # Ramping down from 110 to 50 RPM
            current_rpm = 110.0 - ((cycle - 0.5) * 2.0) * 60.0
            
        rev += 1
        server.update(rev, current_rpm=current_rpm)
        
        expected_power = int(current_rpm * 0.8 + 30.0)
        print(f"[Diag] Elapsed: {elapsed:4.1f}s | RPM: {current_rpm:5.1f} | Power: {expected_power:3d}W | Status: {server.status}")
        
        # Sleep for 1 revolution at the current RPM
        sleep_time = 60.0 / current_rpm
        time.sleep(sleep_time)
        
except KeyboardInterrupt:
    print("\n[Diag] Stopping...")
finally:
    server.stop()
    print("[Diag] Done.")
