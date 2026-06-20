"""VeloTracker - Color detector module."""

import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional

import config


@dataclass
class DetectionResult:
    cx: int
    cy: int
    raw_cx: int
    raw_cy: int
    contour: np.ndarray
    area: float
    mask: np.ndarray
    detection_confidence: float


class ColorDetector:
    def __init__(self, hsv_lower=None, hsv_upper=None):
        self._hsv_lower = hsv_lower if hsv_lower is not None else config.HSV_LOWER.copy()
        self._hsv_upper = hsv_upper if hsv_upper is not None else config.HSV_UPPER.copy()
        self._hsv_lower_init = self._hsv_lower.copy()
        self._hsv_upper_init = self._hsv_upper.copy()
        self._initial_mean_hsv = None
        k = config.MORPH_KERNEL_SIZE
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        self._smooth_cx: Optional[float] = None  # kept for backward compat in DetectionResult.cx
        
        # Initialize CLAHE for Value/Brightness normalization
        clip_limit = getattr(config, "CLAHE_CLIP_LIMIT", 3.0)
        tile_size = getattr(config, "CLAHE_TILE_SIZE", 8)
        self._clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))

    def detect(self, frame, expected_position=None, roi_circle=None) -> Optional[DetectionResult]:
        # Determine the crop ROI if roi_circle is provided
        crop_offset = (0, 0)
        processed_frame = frame
        
        if roi_circle is not None:
            cx_c, cy_c, r_c = roi_circle
            # Add safety padding (e.g. 25% of radius, minimum 30 pixels)
            padding = max(30, int(r_c * 0.25))
            x_min = max(0, int(cx_c - r_c - padding))
            y_min = max(0, int(cy_c - r_c - padding))
            x_max = min(frame.shape[1], int(cx_c + r_c + padding))
            y_max = min(frame.shape[0], int(cy_c + r_c + padding))
            
            # Ensure the crop window is valid and has positive area
            if (x_max - x_min) > 10 and (y_max - y_min) > 10:
                crop_offset = (x_min, y_min)
                processed_frame = frame[y_min:y_max, x_min:x_max]

        # Bilateral filter smooths color noise while preserving edges better than Gaussian blur
        blurred = cv2.bilateralFilter(processed_frame, 9, 75, 75)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
        
        # Apply CLAHE to the Value channel to normalize brightness/shadows locally
        h, s, v = cv2.split(hsv)
        v = self._clahe.apply(v)
        hsv = cv2.merge((h, s, v))
        
        crop_mask = cv2.inRange(hsv, self._hsv_lower, self._hsv_upper)
        crop_mask = cv2.morphologyEx(crop_mask, cv2.MORPH_CLOSE, self._kernel, iterations=config.MORPH_ITERATIONS)
        crop_mask = cv2.morphologyEx(crop_mask, cv2.MORPH_OPEN, self._kernel, iterations=1)

        contours, _ = cv2.findContours(crop_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        valid = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < config.MIN_CONTOUR_AREA:
                continue
            perim = cv2.arcLength(c, True)
            if perim == 0:
                continue
            circ = 4 * np.pi * area / (perim ** 2)
            if circ >= config.MIN_CIRCULARITY:
                M = cv2.moments(c)
                if M["m00"] == 0:
                    continue
                # Local coordinates inside the cropped frame
                local_cx = M["m10"] / M["m00"]
                local_cy = M["m01"] / M["m00"]
                
                # Global coordinates for proximity filtering
                global_cx = local_cx + crop_offset[0]
                global_cy = local_cy + crop_offset[1]
                
                # Proximity filtering if expected position/path is known
                if expected_position is not None:
                    if len(expected_position) == 3:
                        # (center_x, center_y, radius)
                        ex_cx, ex_cy, ex_r = expected_position
                        dist = np.sqrt((global_cx - ex_cx)**2 + (global_cy - ex_cy)**2)
                        max_diff = max(25.0, ex_r * 0.3)
                        if abs(dist - ex_r) > max_diff:
                            continue
                    elif len(expected_position) == 2:
                        # (predicted_x, predicted_y)
                        ex_cx, ex_cy = expected_position
                        dist = np.sqrt((global_cx - ex_cx)**2 + (global_cy - ex_cy)**2)
                        if dist > 50.0:
                            continue
                            
                valid.append((c, area, local_cx, local_cy, circ))

        if not valid:
            return None

        # Choose the largest matching contour
        largest, area, local_cx, local_cy, circ = max(valid, key=lambda x: x[1])

        # Adaptive HSV range update based on the matched sticker contour (local frame)
        c_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        cv2.drawContours(c_mask, [largest], -1, 255, -1)
        mean_hsv = np.array(cv2.mean(hsv, mask=c_mask)[:3])

        if self._initial_mean_hsv is None:
            self._initial_mean_hsv = mean_hsv.copy()

        drift = mean_hsv - self._initial_mean_hsv
        target_lower = self._hsv_lower_init + drift
        target_upper = self._hsv_upper_init + drift

        alpha = 0.02
        self._hsv_lower = (1.0 - alpha) * self._hsv_lower + alpha * target_lower
        self._hsv_upper = (1.0 - alpha) * self._hsv_upper + alpha * target_upper

        self._hsv_lower = np.clip(self._hsv_lower, self._hsv_lower_init - 15.0, self._hsv_lower_init + 15.0)
        self._hsv_upper = np.clip(self._hsv_upper, self._hsv_upper_init - 15.0, self._hsv_upper_init + 15.0)

        self._hsv_lower = np.clip(self._hsv_lower, [0, 0, 0], [179, 255, 255]).astype(np.uint8)
        self._hsv_upper = np.clip(self._hsv_upper, [0, 0, 0], [179, 255, 255]).astype(np.uint8)

        # Translate contour and raw coordinates back to global space
        raw_cx = int(round(local_cx + crop_offset[0]))
        raw_cy = int(round(local_cy + crop_offset[1]))
        global_largest = largest + np.array([crop_offset[0], crop_offset[1]], dtype=np.int32)

        # BUG #15: removed redundant EMA smoothing (α=0.8 was effectively no-op,
        # and the Kalman filter in RPMCalculator already subsumes this role).
        # DetectionResult.cx/cy now return raw_cx/raw_cy directly.

        # Reconstruct full frame mask for the dashboard debug view
        full_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        if crop_offset != (0, 0):
            full_mask[y_min:y_max, x_min:x_max] = crop_mask
        else:
            full_mask = crop_mask

        return DetectionResult(
            cx=raw_cx,
            cy=raw_cy,
            raw_cx=raw_cx,
            raw_cy=raw_cy,
            contour=global_largest,
            area=area,
            mask=full_mask,
            detection_confidence=min(1.0, max(0.0, float(circ)))
        )
