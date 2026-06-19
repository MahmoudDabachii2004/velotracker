"""VeloTracker - RPM calculator module."""

import math
import time
from collections import deque
from typing import Optional, Tuple

import numpy as np
import config


class StickerKalmanFilter:
    def __init__(self, q: float = 100000.0, r: float = 9.0):
        self.q = q
        self.r = r
        self.state = None
        self.P = np.eye(4) * 10.0
        self.last_time = None

    def reset(self):
        self.state = None
        self.P = np.eye(4) * 10.0
        self.last_time = None

    def predict_and_update(self, x: float, y: float, timestamp: float) -> Tuple[float, float]:
        if self.last_time is None or self.state is None:
            self.state = np.array([x, y, 0.0, 0.0])
            self.P = np.eye(4) * 10.0
            self.last_time = timestamp
            return x, y

        dt = timestamp - self.last_time
        if dt <= 0:
            return float(self.state[0]), float(self.state[1])

        dt = min(dt, 0.2)

        F = np.array([
            [1.0, 0.0,  dt, 0.0],
            [0.0, 1.0, 0.0,  dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ])

        dt2 = dt * dt
        dt3 = dt2 * dt
        Q = self.q * np.array([
            [dt3 / 3.0, 0.0,       dt2 / 2.0, 0.0],
            [0.0,       dt3 / 3.0, 0.0,       dt2 / 2.0],
            [dt2 / 2.0, 0.0,       dt,        0.0],
            [0.0,       dt2 / 2.0, 0.0,       dt]
        ])

        self.state = np.dot(F, self.state)
        self.P = np.dot(F, np.dot(self.P, F.T)) + Q

        H = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0]
        ])
        R = np.eye(2) * self.r
        z = np.array([x, y])
        y_residual = z - np.dot(H, self.state)
        S = np.dot(H, np.dot(self.P, H.T)) + R
        
        try:
            S_inv = np.linalg.inv(S)
            K = np.dot(self.P, np.dot(H.T, S_inv))
            self.state = self.state + np.dot(K, y_residual)
            self.P = np.dot(np.eye(4) - np.dot(K, H), self.P)
        except np.linalg.LinAlgError:
            self.reset()
            self.state = np.array([x, y, 0.0, 0.0])

        self.last_time = timestamp
        return float(self.state[0]), float(self.state[1])


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
        self._omega = 0.0
        self._alpha = 0.0
        self._kalman = StickerKalmanFilter()

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
        self._omega = 0.0
        self._alpha = 0.0
        self._kalman.reset()

    def _fit_circle(self, positions):
        """Taubin algebraic circle fit — better than Kasa for partial arcs."""
        pts = np.array(positions, dtype=np.float64)
        n = len(pts)
        if n < 5:
            return None, 0.0, float('inf')

        # Centering data for numerical stability
        centroid = np.mean(pts, axis=0)
        u = pts[:, 0] - centroid[0]
        v = pts[:, 1] - centroid[1]
        z = u**2 + v**2

        # Compute moments
        Mxx = np.mean(u**2)
        Myy = np.mean(v**2)
        Mxy = np.mean(u * v)
        Mxz = np.mean(u * z)
        Myz = np.mean(v * z)
        Mzz = np.mean(z**2)

        M_z = Mxx + Myy
        Var_z = Mzz - M_z**2

        # Form reduced moments matrix
        M_reduced = np.array([
            [Var_z, Mxz, Myz],
            [Mxz, Mxx, Mxy],
            [Myz, Mxy, Myy]
        ])

        # Form inverse of reduced constraint matrix N
        if abs(M_z) < 1e-10:
            return None, 0.0, float('inf')

        N_inv = np.array([
            [1.0 / (4.0 * M_z), 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0]
        ])

        try:
            # Solve standard eigenvalue problem
            D = np.dot(N_inv, M_reduced)
            eigenvalues, eigenvectors = np.linalg.eig(D)

            # Convert to real to handle numerical noise
            eigenvalues = np.real(eigenvalues)
            eigenvectors = np.real(eigenvectors)

            # Find the smallest positive eigenvalue
            pos_idx = np.where(eigenvalues > 1e-10)[0]
            if len(pos_idx) == 0:
                pos_idx = np.where(eigenvalues >= 0)[0]
                if len(pos_idx) == 0:
                    return None, 0.0, float('inf')

            min_pos_idx = pos_idx[np.argmin(eigenvalues[pos_idx])]
            v_eigen = eigenvectors[:, min_pos_idx]

            A1, A2, A3 = v_eigen[0], v_eigen[1], v_eigen[2]
            A4 = -M_z * A1

            if abs(A1) < 1e-10:
                return None, 0.0, float('inf')

            # Reconstruct center and radius
            u_c = -A2 / (2.0 * A1)
            v_c = -A3 / (2.0 * A1)

            r2 = u_c**2 + v_c**2 - A4 / A1
            if r2 < 0:
                return None, 0.0, float('inf')

            r = np.sqrt(r2)
            center = (u_c + centroid[0], v_c + centroid[1])

            # Compute residual error (std of distances from fitted radius)
            dists = np.sqrt((pts[:, 0] - center[0])**2 + (pts[:, 1] - center[1])**2)
            std = np.std(dists)
            cv = std / r if r > 0 else float('inf')

            return center, r, cv
        except Exception:
            return None, 0.0, float('inf')

    def update(self, cx: int, cy: int, timestamp: Optional[float] = None):
        if timestamp is None:
            timestamp = time.time()
        self._last_detection_time = timestamp

        if self._phase == self.PHASE_TRACKING:
            if self._kalman.last_time is not None and (timestamp - self._kalman.last_time > config.RPM_TIMEOUT_SEC):
                self._kalman.reset()
            cx_filt, cy_filt = self._kalman.predict_and_update(cx, cy, timestamp)
            cx = int(round(cx_filt))
            cy = int(round(cy_filt))

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
        self._estimate_rpm(timestamp)
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
            # Extrapolate omega using angular acceleration (alpha)
            omega_pred = self._omega + self._alpha * time_since
            # Safety checks:
            # 1. Don't reverse direction during prediction
            if np.sign(omega_pred) != np.sign(self._omega):
                omega_pred = 0.0
            # 2. Cap prediction to not exceed 1.5x the last known omega to prevent run-away acceleration predictions
            if abs(omega_pred) > 1.5 * abs(self._omega):
                omega_pred = np.sign(self._omega) * 1.5 * abs(self._omega)
                
            pa = self._prev_angle + omega_pred * dt
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
            self._estimate_rpm(timestamp)
            current_revs = int(abs(self._total_angle) / (2 * math.pi))
            if current_revs > self._rev_count:
                self._rev_count = current_revs
                self._last_rev_event_time = timestamp
        else:
            self._is_predicted = False
            # Decay factor when tracking is fully lost
            decay_factor = getattr(config, "RPM_DECAY_FACTOR", 0.85)
            self._current_rpm *= decay_factor
            if self._current_rpm < 1.0:
                self._current_rpm = 0.0

    def _estimate_rpm(self, timestamp: float):
        if len(self._angular_window) < 5:
            return
            
        timestamps = np.array([pt[0] for pt in self._angular_window])
        thetas = np.array([pt[1] for pt in self._angular_window])
        
        # Calculate weights using exponential decay (recent samples get higher weight)
        decay = getattr(config, "RPM_WEIGHTED_OLS_LAMBDA", 2.0)
        weights = np.exp(-decay * (timestamp - timestamps))
        
        # Center time vector to make it numerically stable
        t0 = timestamps[0]
        x = timestamps - t0
        y = thetas
        
        Sw = np.sum(weights)
        Sx = np.sum(weights * x)
        Sy = np.sum(weights * y)
        Sxx = np.sum(weights * x * x)
        Sxy = np.sum(weights * x * y)
        
        denom = Sw * Sxx - Sx**2
        omega = (Sw * Sxy - Sx * Sy) / denom if abs(denom) > 1e-6 else 0.0
        
        # Track rotation direction
        if abs(omega) > 0.1:
            self._rotation_direction = 1.0 if omega > 0 else -1.0
            
        # Calculate angular acceleration alpha = d_omega / dt with smoothing
        prev_omega = self._omega
        self._omega = omega
        if len(self._angular_window) >= 2:
            dt = timestamps[-1] - timestamps[-2]
            if dt > 0:
                raw_alpha = (omega - prev_omega) / dt
                self._alpha = 0.1 * raw_alpha + 0.9 * self._alpha
            else:
                self._alpha = 0.0
        else:
            self._alpha = 0.0
            
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
            # Smooth EMA update
            ema_alpha = getattr(config, "RPM_EMA_ALPHA", 0.15)
            self._current_rpm = ema_alpha * target + (1 - ema_alpha) * self._current_rpm

    def check_timeout(self, current_time=None):
        if current_time is None:
            current_time = time.time()
        ts = current_time - self._last_detection_time
        if ts > config.RPM_TIMEOUT_SEC:
            # If we timed out, stop immediately and cancel prediction
            self._current_rpm = 0.0
            self._is_predicted = False

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
    @property
    def alpha(self): return self._alpha
    @property
    def omega(self): return self._omega
