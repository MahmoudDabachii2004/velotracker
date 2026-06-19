"""VeloTracker - Main entry point (cross-platform).

Usage:
    python3 main.py                  Normal mode
    python3 main.py --no-ble         Vision-only test (no BLE)
    python3 main.py --camera 1       Use camera index 1 (often IriunWebcam)
    python3 main.py --debug          Show detection mask

Keys:
    Q = Quit
    C = Recalibrate rotation center
    R = Full reset
"""

import sys
import os
import argparse
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from modules.camera import Camera
from modules.detector import ColorDetector
from modules.rpm_calculator import RPMCalculator
from modules.dashboard import Dashboard
from modules.ble_server import BLECadenceServer


def parse_args():
    p = argparse.ArgumentParser(description="VeloTracker - Bike -> MyWhoosh")
    p.add_argument("--camera", type=int, default=config.CAMERA_INDEX,
                   help=f"Camera index (default: {config.CAMERA_INDEX})")
    p.add_argument("--no-ble", action="store_true",
                   help="Disable BLE (vision-only test)")
    p.add_argument("--debug", action="store_true",
                   help="Show detection mask")
    return p.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("  VeloTracker - Bike -> MyWhoosh")
    print("=" * 60)
    print()

    # Camera
    try:
        camera = Camera(camera_index=args.camera)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Detector
    detector = ColorDetector()
    print(f"[Main] HSV: {config.HSV_LOWER} -> {config.HSV_UPPER}")

    # RPM calculator
    rpm_calc = RPMCalculator()

    # Dashboard
    dashboard = Dashboard()

    # BLE
    ble_server = None
    if not args.no_ble:
        try:
            ble_server = BLECadenceServer()
            ble_server.start()
            time.sleep(1.0)
        except Exception as e:
            print(f"[Main] WARNING: BLE failed: {e}")
            ble_server = None
    else:
        print("[Main] BLE disabled (vision-only test)")

    print()
    print("[Main] Ready! Put a colored sticker on the pedal and pedal.")
    print("[Main] Keys: Q=Quit  C=Recalibrate  R=Reset")
    print()

    window_name = "VeloTracker"
    cv2.namedWindow(window_name, cv2.WINDOW_AUTOSIZE)

    try:
        while True:
            ok, frame = camera.read()
            if not ok:
                print("[Main] Camera read error")
                break

            detection = detector.detect(frame)
            now = time.time()
            if detection is not None:
                rpm_calc.update(detection.cx, detection.cy, now)
            else:
                rpm_calc.update_lost(now)
            rpm_calc.check_timeout(now)

            if ble_server is not None:
                ble_server.update(
                    rpm_calc.revolutions,
                    rpm_calc.last_rev_event_time,
                    current_rpm=rpm_calc.rpm,
                )

            ble_status = ble_server.status if ble_server else "Off"
            display = dashboard.draw(
                frame=frame,
                detection=detection,
                rpm_calc=rpm_calc,
                ble_status=ble_status,
                fps=camera.fps,
                debug_mask=args.debug,
            )

            cv2.imshow(window_name, display)

            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('c'):
                rpm_calc.reset_center()
                print("[Main] Center reset. Pedal to recalibrate...")
            elif key == ord('r'):
                rpm_calc.reset()
                print("[Main] Full reset. Pedal to recalibrate...")

    except KeyboardInterrupt:
        print("\n[Main] Ctrl+C - stopping...")
    finally:
        print("[Main] Cleaning up...")
        camera.release()
        if ble_server is not None:
            ble_server.stop()
        cv2.destroyAllWindows()
        print("[Main] Done.")


if __name__ == "__main__":
    main()
