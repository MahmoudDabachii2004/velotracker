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
        k = config.MORPH_KERNEL_SIZE
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        self._smooth_cx: Optional[float] = None
        self._smooth_cy: Optional[float] = None
        
        # Initialize CLAHE for Value/Brightness normalization
        clip_limit = getattr(config, "CLAHE_CLIP_LIMIT", 3.0)
        tile_size = getattr(config, "CLAHE_TILE_SIZE", 8)
        self._clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))

    def detect(self, frame, expected_position=None) -> Optional[DetectionResult]:
        # Bilateral filter smooths color noise while preserving edges better than Gaussian blur
        blurred = cv2.bilateralFilter(frame, 9, 75, 75)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
        
        # Apply CLAHE to the Value channel to normalize brightness/shadows locally
        h, s, v = cv2.split(hsv)
        v = self._clahe.apply(v)
        hsv = cv2.merge((h, s, v))
        
        mask = cv2.inRange(hsv, self._hsv_lower, self._hsv_upper)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel, iterations=config.MORPH_ITERATIONS)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel, iterations=1)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
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
                cx = M["m10"] / M["m00"]
                cy = M["m01"] / M["m00"]
                
                # Proximity filtering if expected position/path is known
                if expected_position is not None:
                    if len(expected_position) == 3:
                        # (center_x, center_y, radius)
                        ex_cx, ex_cy, ex_r = expected_position
                        dist = np.sqrt((cx - ex_cx)**2 + (cy - ex_cy)**2)
                        max_diff = max(25.0, ex_r * 0.3)
                        if abs(dist - ex_r) > max_diff:
                            continue
                    elif len(expected_position) == 2:
                        # (predicted_x, predicted_y)
                        ex_cx, ex_cy = expected_position
                        dist = np.sqrt((cx - ex_cx)**2 + (cy - ex_cy)**2)
                        if dist > 50.0:
                            continue
                            
                valid.append((c, area, cx, cy, circ))

        if not valid:
            return None

        # Choose the largest matching contour
        largest, area, cx, cy, circ = max(valid, key=lambda x: x[1])
        raw_cx = int(cx)
        raw_cy = int(cy)

        if self._smooth_cx is None:
            self._smooth_cx = float(raw_cx)
            self._smooth_cy = float(raw_cy)
        else:
            a = 0.8
            self._smooth_cx = a * raw_cx + (1 - a) * self._smooth_cx
            self._smooth_cy = a * raw_cy + (1 - a) * self._smooth_cy

        return DetectionResult(
            cx=int(round(self._smooth_cx)),
            cy=int(round(self._smooth_cy)),
            raw_cx=raw_cx,
            raw_cy=raw_cy,
            contour=largest,
            area=area,
            mask=mask,
            detection_confidence=min(1.0, max(0.0, float(circ)))
        )
