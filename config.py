"""
VeloTracker - Configuration (cross-platform)
Edit this file to match your setup.
"""

import numpy as np

# ============================================================================
# STICKER COLOR (HSV)
# Run `python3 calibrate.py` to find the right values for your sticker.
#
# Default: GREEN sticker (H 36-66 in OpenCV)
#   Orange: H 10-25 | Red: H 0-10 + 170-180 | Yellow: H 25-35
#   Green: H 36-66   | Blue: H 90-130
# ============================================================================
HSV_LOWER = np.array([38, 35, 52])
HSV_UPPER = np.array([68, 255, 255])

# ============================================================================
# CAMERA
# ============================================================================
CAMERA_INDEX = 0          # 0=built-in webcam, 1=iPhone via IriunWebcam (usually)
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30

# ============================================================================
# DETECTION
# ============================================================================
MIN_CONTOUR_AREA = 500
MORPH_KERNEL_SIZE = 5
MORPH_ITERATIONS = 2
MIN_CIRCULARITY = 0.5

# ============================================================================
# RPM CALCULATION
# ============================================================================
CALIBRATION_FRAMES = 90
MIN_RADIUS_PX = 20
RPM_SMOOTHING_WINDOW = 5
RPM_MIN = 18
RPM_MAX = 200
RPM_TIMEOUT_SEC = 3.0
RPM_REGRESSION_WINDOW = 20
TRACKING_FIT_WINDOW = 150

# ============================================================================
# BLE (via bless — CoreBluetooth on macOS, WinRT on Windows, BlueZ on Linux)
# ============================================================================
# IMPORTANT: Keep the name <=10 chars. macOS BLE advertisements are limited
# to 28 bytes. If the name is >10 chars, bless may drop the service UUIDs
# from the advertisement, so apps like MyWhoosh can't find the device.
BLE_DEVICE_NAME = "VeloTrack"  # 9 chars - safe
BLE_NOTIFY_INTERVAL_SEC = 0.5  # 2Hz heartbeat (PeloMon-proven value)
WHEEL_TO_CRANK_RATIO = 2.0          # 1 pedal rev = 2 wheel revs (50x11)
WHEEL_CIRCUMFERENCE_M = 2.105       # 700x25C road tire

# ============================================================================
# DASHBOARD COLORS
# ============================================================================
TRAIL_LENGTH = 30
FONT_SCALE_LARGE = 1.5
FONT_SCALE_MEDIUM = 0.7
FONT_SCALE_SMALL = 0.5
COLOR_GREEN = (0, 255, 0)
COLOR_YELLOW = (0, 255, 255)
COLOR_RED = (0, 0, 255)
COLOR_CYAN = (255, 255, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_BLUE = (255, 100, 0)
