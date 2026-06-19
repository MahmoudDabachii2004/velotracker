"""VeloTracker - RPM calculator module."""

import math
import time
from collections import deque
from typing import Optional, Tuple

import numpy as np
import config


class RPMCalculator:
    PHASE_WAITING = "WAITING"
    PHASE_CALIBRATING = "CALIBRATING"
    PHASE_TRACKING = "TRACKING"

    EMA_RPM = 0.15
    MAX_DELTA_ANGLE = 1.0

    def __init__(self):
        self.reset()

    def reset(self):
        self._center: Optional[Tuple[float, float]] = None
        self._radius: float = 0.0
        self._cal_positions: list = []
        self._phase: str = self.PHASE_WAITING
        self._prev_angle: Optional[float] = None
        self._total_angle: float = 0.0
        self._rev_count: int = 0
        self._last_rev_event_time: float = time.time()
        self._current_rpm: float = 0.0
        self._raw_rpm: float = 0.0
        self._angular_window: deque = deque(maxlen=config.RPM_REGRESSION_WINDOW)
        self._tracking_positions: deque = deque(maxlen=config.TRACKING_FIT_WINDOW)
        self._last_detection_time: float = time.time()
        self._last_update_time: float = time.time()
        self._trail: deque = deque(maxlen=config.TRAIL_LENGTH)
        self._is_predicted = False
        self._predicted_position: Optional[Tuple[float, float]] = None
        self._rotation_direction = 1.0

    def reset_center(self):
        self._center = None
        self._radius = 0.0
        self._cal_positions = []
        self._phase = self.PHASE_WAITING
        self._prev_angle = None
        self._total_angle = 0.0
        self._angular_window.clear()
        self._tracking_positions.clear()
        self._current_rpm = 0.0
        self._is_predicted = False

    def _fit_circle(self, positions):
        """Kasa algebraic circle fit. Returns (center, radius, cv)."""
        n = len(positions)
        if n < 5:
            return None, 0.0, float('inf')

        sum_x = sum(p[0] for p in positions)
        sum_y = sum(p[1] for p in positions)
        sum_x2 = sum(p[0]**2 for p in positions)
        sum_y2 = sum(p[1]**2 for p in positions)
        sum_xy = sum(p[0]*p[1] for p in positions)
        sum_x3 = sum(p[0]**3 for p in positions)
        sum_y3 = sum(p[1]**3 for p in positions)
        sum_x2y = sum(p[0]**2 * p[1] for p in positions)
        sum_xy2 = sum(p[0] * p[1]**2 for p in positions)

        A = n * sum_x2 - sum_x**2
        B = n * sum_xy - sum_x * sum_y
        C = n * sum_y2 - sum_y**2
        D = 0.5 * (n * (sum_x3 + sum_xy2) - sum_x * (sum_x2 + sum_y2))
        E = 0.5 * (n * (sum_x2y + sum_y3) - sum_y * (sum_x2 + sum_y2))

        denom = A * C - B**2
        if abs(denom) < 1e-10:
            return None, 0.0, float('inf')

        cx = (D * C - B * E) / denom
        cy = (A * E - B * D) / denom

        dists = [math.sqrt((p[0]-cx)**2 + (p[1]-cy)**2) for p in positions]
        r = sum(dists) / n
        if r > 0:
            std = (sum((d-r)**2 for d in dists) / n) ** 0.5
            cv = std / r
        else:
            cv = float('inf')
        return (cx, cy), r, cv

    def update(self, cx: int, cy: int, timestamp: Optional[float] = None):
        if timestamp is None:
            timestamp = time.time()
        self._last_detection_time = timestamp
        self._trail.append((cx, cy))

        if self._phase == self.PHASE_WAITING:
            self._phase = self.PHASE_CALIBRATING
            self._cal_positions = [(cx, cy)]
            return
        if self._phase == self.PHASE_CALIBRATING:
            self._calibrate(cx, cy, timestamp)
            return
        if self._phase == self.PHASE_TRACKING:
            self._track(cx, cy, timestamp)

    def _calibrate(self, cx, cy, timestamp):
        self._cal_positions.append((cx, cy))
        if len(self._cal_positions) < config.CALIBRATION_FRAMES:
            return
        center, radius, cv = self._fit_circle(self._cal_positions)
        if center and radius >= config.MIN_RADIUS_PX and cv < 0.5:
            self._center = center
            self._radius = radius
            self._phase = self.PHASE_TRACKING
            self._prev_angle = None
            self._total_angle = 0.0
            self._angular_window.clear()
            self._tracking_positions.clear()
            for p in self._cal_positions:
                self._tracking_positions.append(p)
            print(f"[RPM] Center: ({center[0]:.0f},{center[1]:.0f}), "
                  f"radius={radius:.0f}px, CV={cv:.2f}")
        else:
            keep = len(self._cal_positions) // 3
            self._cal_positions = self._cal_positions[-keep:]
            if center is None or radius < config.MIN_RADIUS_PX:
                print("[RPM] Radius too small, keep pedaling...")
            else:
                print(f"[RPM] Not circular (CV={cv:.2f}), pedal evenly...")

    def _track(self, cx, cy, timestamp):
        if self._center is None:
            return
        self._is_predicted = False
        self._tracking_positions.append((cx, cy))
        if len(self._tracking_positions) >= 60:
            center, radius, cv = self._fit_circle(self._tracking_positions)
            if center and radius >= config.MIN_RADIUS_PX and cv < 0.3:
                self._center = center
                self._radius = radius

        dx = cx - self._center[0]
        dy = cy - self._center[1]
        if math.sqrt(dx*dx + dy*dy) < 5:
            return

        angle = math.atan2(dy, dx)
        if self._prev_angle is None:
            self._prev_angle = angle
            self._last_update_time = timestamp
            return

        delta = angle - self._prev_angle
        if delta > math.pi:
            delta -= 2 * math.pi
        elif delta < -math.pi:
            delta += 2 * math.pi

        dt = timestamp - self._last_update_time
        if dt <= 0:
            return

        max_delta = self.MAX_DELTA_ANGLE * (dt / 0.033)
        if abs(delta) > max_delta:
            self._prev_angle = angle
            return

        self._total_angle += delta
        self._prev_angle = angle
        self._last_update_time = timestamp
        self._angular_window.append((timestamp, self._total_angle))
        self._estimate_rpm()
        current_revs = int(abs(self._total_angle) / (2 * math.pi))
        if current_revs > self._rev_count:
            self._rev_count = current_revs
            self._last_rev_event_time = timestamp

    def update_lost(self, timestamp: float):
        if self._phase != self.PHASE_TRACKING or self._center is None:
            self._is_predicted = False
            return
        # If we haven't established a previous angle yet (just started tracking
        # but lost detection immediately), we can't predict. Just bail out.
        if self._prev_angle is None:
            self._is_predicted = False
            return
        dt = timestamp - self._last_update_time
        time_since = timestamp - self._last_detection_time
        if dt <= 0:
            return
        if time_since <= config.RPM_TIMEOUT_SEC:
            self._is_predicted = True
            omega = self._rotation_direction * (self._current_rpm * 2 * math.pi / 60.0)
            pa = self._prev_angle + omega * dt
            pred_x = self._center[0] + self._radius * math.cos(pa)
            pred_y = self._center[1] + self._radius * math.sin(pa)
            self._predicted_position = (pred_x, pred_y)
            self._trail.append((int(round(pred_x)), int(round(pred_y))))
            delta = pa - self._prev_angle
            if delta > math.pi:
                delta -= 2 * math.pi
            elif delta < -math.pi:
                delta += 2 * math.pi
            self._total_angle += delta
            self._prev_angle = pa
            self._last_update_time = timestamp
            self._angular_window.append((timestamp, self._total_angle))
            self._estimate_rpm()
            current_revs = int(abs(self._total_angle) / (2 * math.pi))
            if current_revs > self._rev_count:
                self._rev_count = current_revs
                self._last_rev_event_time = timestamp
        else:
            self._is_predicted = False
            self._current_rpm *= 0.85
            if self._current_rpm < 1.0:
                self._current_rpm = 0.0

    def _estimate_rpm(self):
        if len(self._angular_window) < 5:
            return
        n = len(self._angular_window)
        t0 = self._angular_window[0][0]
        sx = sy = sxx = sxy = 0.0
        for t, theta in self._angular_window:
            x = t - t0
            y = theta
            sx += x; sy += y; sxx += x*x; sxy += x*y
        denom = n * sxx - sx**2
        omega = (n * sxy - sx * sy) / denom if abs(denom) > 1e-6 else 0.0
        if abs(omega) > 0.1:
            self._rotation_direction = 1.0 if omega > 0 else -1.0
        self._raw_rpm = (abs(omega) / (2 * math.pi)) * 60.0

        if self._raw_rpm < config.RPM_MIN:
            target = 0.0
        elif self._raw_rpm > config.RPM_MAX:
            target = self._current_rpm
        else:
            target = self._raw_rpm

        if self._current_rpm == 0 and target > 0:
            self._current_rpm = target
        elif target == 0:
            self._current_rpm *= 0.80
            if self._current_rpm < 1.0:
                self._current_rpm = 0.0
        else:
            self._current_rpm = self.EMA_RPM * target + (1 - self.EMA_RPM) * self._current_rpm

    def check_timeout(self, current_time=None):
        if current_time is None:
            current_time = time.time()
        ts = current_time - self._last_detection_time
        if ts > config.RPM_TIMEOUT_SEC:
            self._current_rpm *= 0.80
            if self._current_rpm < 1.0:
                self._current_rpm = 0.0
            self._is_predicted = False
        elif ts > 0.5 and not self._is_predicted:
            self._current_rpm *= 0.90

    @property
    def rpm(self): return self._current_rpm
    @property
    def raw_rpm(self): return self._raw_rpm
    @property
    def revolutions(self): return self._rev_count
    @property
    def last_rev_event_time(self): return self._last_rev_event_time
    @property
    def center(self): return self._center
    @property
    def radius(self): return self._radius
    @property
    def phase(self): return self._phase
    @property
    def is_predicted(self): return self._is_predicted
    @property
    def predicted_position(self): return self._predicted_position
    @property
    def calibration_progress(self):
        if self._phase != self.PHASE_CALIBRATING:
            return 1.0 if self._phase == self.PHASE_TRACKING else 0.0
        return len(self._cal_positions) / config.CALIBRATION_FRAMES
    @property
    def trail(self): return list(self._trail)
