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
HSV_LOWER = np.array([35, 114, 95])
HSV_UPPER = np.array([65, 255, 255])

# ============================================================================
# CAMERA
# ============================================================================
CAMERA_INDEX = 1          # 0=built-in webcam, 1=iPhone via IriunWebcam (usually)
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
CLAHE_CLIP_LIMIT = 3.0
CLAHE_TILE_SIZE = 8       # Size of grid for local contrast equalization (e.g. 8x8)

# ============================================================================
# RPM CALCULATION
# ============================================================================
CALIBRATION_FRAMES = 90
MIN_RADIUS_PX = 20
RPM_SMOOTHING_WINDOW = 5
RPM_MIN = 18
RPM_MAX = 200
RPM_TIMEOUT_SEC = 3.0
# Tier 3 BUG #16: RPM_REGRESSION_WINDOW is now TIME-BASED (seconds), not sample-count.
# At 30 FPS, 0.67s = 20 samples (matches the old default).
# At 15 FPS (IriunWebcam slow), 0.67s = 10 samples (the time window stays the same).
# This makes the RPM regression behavior consistent regardless of frame rate.
# Set to 0 to use the legacy sample-count behavior (RPM_REGRESSION_SAMPLES).
RPM_REGRESSION_WINDOW_SEC = 0.67
RPM_REGRESSION_SAMPLES = 20  # legacy fallback if RPM_REGRESSION_WINDOW_SEC = 0
TRACKING_FIT_WINDOW = 150

# Advanced RPM Tuning
RPM_DECAY_FACTOR = 0.85       # Per-frame decay factor when detection is lost
RPM_EMA_ALPHA = 0.15          # Smoothing coefficient (EMA) for dashboard RPM
RPM_WEIGHTED_OLS_LAMBDA = 2.0 # Exponential decay factor for weighted least-squares regression

# ============================================================================
# BLE (via bless — CoreBluetooth on macOS, WinRT on Windows, BlueZ on Linux)
# ============================================================================
# IMPORTANT: Keep the name <=10 chars. macOS BLE advertisements are limited
# to 28 bytes. If the name is >10 chars, bless may drop the service UUIDs
# from the advertisement, so apps like MyWhoosh can't find the device.
#
# On Windows, the device name shown in MyWhoosh will fall back to the
# system adapter name (e.g. 'Device-XXXXXX') regardless of this setting —
# this is a fundamental WinRT limitation, not a bless bug. See 'Known Issues'
# in README.md for details.
BLE_DEVICE_NAME = "V"  # 1 char - absolute minimum advertising payload size
BLE_NOTIFY_INTERVAL_SEC = 0.5  # 2Hz heartbeat (PeloMon-proven value)

# ============================================================================
# DRIVETRAIN GEOMETRY
# Used for speed calculation: speed_kmh = RPM * (CHAINRING/COG) * WHEEL_CIRCUMFERENCE_M * 60 / 1000
# Common gear ratios for indoor training (50x17 is the FTP-test standard):
#   50x11 = 4.545 (sprint)
#   50x17 = 2.941 (FTP test / sweet spot)
#   50x19 = 2.632 (endurance)
#   34x17 = 2.000 (recovery / small ring)
#   50x25 = 2.000 (climbing simulation)
# For a single-speed spin bike: set CHAINRING = COG = 1 (ratio = 1.0)
# ============================================================================
CHAINRING = 50
COG = 17
WHEEL_TO_CRANK_RATIO = CHAINRING / COG   # 2.941 for 50x17
WHEEL_CIRCUMFERENCE_M = 2.105            # 700x25C road tire (ETRTO 25-622)

# ============================================================================
# POWER SIMULATION (zPower)
# ============================================================================
# Choice of model for estimated power (Watts):
#   "linear" -> Simple linear approximation: Watts = RPM * 0.8 + 30
#               (debug only — not accurate to any real trainer)
#   "fluid"  -> Kurt Kinetic Road Machine official power curve
#               Source: https://kurtkinetic.com/ (verified against their example:
#               16.1 mph -> 164W, 20 mph -> 258W, 25 mph -> 431W)
#               Formula: Power = 5.244820 * S + 0.019168 * S^3 (where S is speed in mph)
#   "mag"    -> Generic magnetic trainer (quadratic estimate, ±20-30% uncertainty)
#               Formula: Power = 0.1 * S^2 + 3.0 * S + 10 (where S is speed in km/h)
#
# NOTE: zPower is an ESTIMATE based on a fixed trainer model. Real-world accuracy
# depends on tire pressure, roller pressure, fluid temperature, and tire compound.
# Expected accuracy: ±10-25% vs a real strain-gauge power meter.
# Use for relative training trends, NOT for absolute FTP determination.
POWER_MODEL = "fluid"

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
