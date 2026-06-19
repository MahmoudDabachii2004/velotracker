"""VeloTracker - Sticker color calibration (cross-platform).

Usage:
    python3 calibrate.py
    python3 calibrate.py --camera 1

Controls:
    - Click on the sticker to auto-detect its color
    - Adjust trackbars to refine
    - S = Save to config.py
    - Q = Quit
"""

import sys
import os
import argparse
import re
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from modules.camera import Camera


class Calibrator:
    def __init__(self):
        self._win = "VeloTracker Calibration"
        self._h_min = int(config.HSV_LOWER[0])
        self._s_min = int(config.HSV_LOWER[1])
        self._v_min = int(config.HSV_LOWER[2])
        self._h_max = int(config.HSV_UPPER[0])
        self._s_max = int(config.HSV_UPPER[1])
        self._v_max = int(config.HSV_UPPER[2])
        self._clicked_hsv = None

    def _on_click(self, event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        hsv = param
        if hsv is None:
            return
        h, s, v = hsv[y, x]
        self._clicked_hsv = (h, s, v)
        self._h_min = max(0, int(h) - 15)
        self._h_max = min(179, int(h) + 15)
        self._s_min = max(0, int(s) - 50)
        self._s_max = 255
        self._v_min = max(0, int(v) - 50)
        self._v_max = 255
        for name, val in [("H min", self._h_min), ("H max", self._h_max),
                          ("S min", self._s_min), ("S max", self._s_max),
                          ("V min", self._v_min), ("V max", self._v_max)]:
            cv2.setTrackbarPos(name, self._win, val)
        print(f"[Calib] Click HSV=({h},{s},{v}) -> [{self._h_min},{self._s_min},{self._v_min}] to [{self._h_max},{self._s_max},{self._v_max}]")

    def _save(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py")
        with open(path, 'r') as f:
            content = f.read()
        content = re.sub(r'HSV_LOWER\s*=\s*np\.array\(\[.*?\]\)',
                         f'HSV_LOWER = np.array([{self._h_min}, {self._s_min}, {self._v_min}])',
                         content)
        content = re.sub(r'HSV_UPPER\s*=\s*np\.array\(\[.*?\]\)',
                         f'HSV_UPPER = np.array([{self._h_max}, {self._s_max}, {self._v_max}])',
                         content)
        with open(path, 'w') as f:
            f.write(content)
        print(f"[Calib] Saved: [{self._h_min},{self._s_min},{self._v_min}] to [{self._h_max},{self._s_max},{self._v_max}]")

    def run(self, camera_index=None):
        idx = camera_index if camera_index is not None else config.CAMERA_INDEX
        try:
            camera = Camera(camera_index=idx)
        except RuntimeError as e:
            print(f"[Calib] ERROR: Cannot open camera index {idx}: {e}")
            return

        cv2.namedWindow(self._win)
        for name, val, mx in [("H min", self._h_min, 179), ("H max", self._h_max, 179),
                              ("S min", self._s_min, 255), ("S max", self._s_max, 255),
                              ("V min", self._v_min, 255), ("V max", self._v_max, 255)]:
            cv2.createTrackbar(name, self._win, val, mx, lambda x: None)

        font = cv2.FONT_HERSHEY_SIMPLEX
        print("\n=== VeloTracker Calibration ===")
        print("  Click on sticker | S: Save | Q: Quit\n")

        try:
            while True:
                ok, frame = camera.read()
                if not ok:
                    break
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

                self._h_min = cv2.getTrackbarPos("H min", self._win)
                self._h_max = cv2.getTrackbarPos("H max", self._win)
                self._s_min = cv2.getTrackbarPos("S min", self._win)
                self._s_max = cv2.getTrackbarPos("S max", self._win)
                self._v_min = cv2.getTrackbarPos("V min", self._win)
                self._v_max = cv2.getTrackbarPos("V max", self._win)

                lower = np.array([self._h_min, self._s_min, self._v_min])
                upper = np.array([self._h_max, self._s_max, self._v_max])
                mask = cv2.inRange(hsv, lower, upper)

                k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                              (config.MORPH_KERNEL_SIZE, config.MORPH_KERNEL_SIZE))
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=config.MORPH_ITERATIONS)
                mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)

                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                display = frame.copy()
                for c in contours:
                    area = cv2.contourArea(c)
                    if area < config.MIN_CONTOUR_AREA:
                        continue
                    perim = cv2.arcLength(c, True)
                    if perim == 0:
                        continue
                    circ = 4 * np.pi * area / (perim ** 2)
                    valid = circ >= config.MIN_CIRCULARITY
                    color = (0, 255, 0) if valid else (0, 0, 255)
                    cv2.drawContours(display, [c], -1, color, 2)
                    M = cv2.moments(c)
                    if M["m00"] > 0:
                        cx = int(M["m10"] / M["m00"])
                        cy = int(M["m01"] / M["m00"])
                        cv2.circle(display, (cx, cy), 5, (255, 0, 0) if valid else (0, 0, 255), -1)
                        cv2.putText(display, f"A={area:.0f} C={circ:.2f}", (cx + 10, cy),
                                    font, 0.4, (255, 255, 255), 1)

                info = f"HSV: [{self._h_min},{self._s_min},{self._v_min}] - [{self._h_max},{self._s_max},{self._v_max}]"
                cv2.putText(display, info, (10, 25), font, 0.5, (0, 255, 255), 1)
                cv2.putText(display, "Click sticker | S:Save | Q:Quit", (10, 45),
                            font, 0.4, (200, 200, 200), 1)
                if self._clicked_hsv:
                    h, s, v = self._clicked_hsv
                    cv2.putText(display, f"Last click: H={h} S={s} V={v}",
                                (10, 65), font, 0.4, (0, 200, 255), 1)

                mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                top = np.hstack([display, mask_bgr])
                cv2.imshow(self._win, top)
                cv2.setMouseCallback(self._win, self._on_click, hsv)

                if cv2.getWindowProperty(self._win, cv2.WND_PROP_VISIBLE) < 1:
                    break

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('s'):
                    self._save()
        finally:
            camera.release()
            cv2.destroyAllWindows()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="VeloTracker Calibration")
    p.add_argument("--camera", type=int, default=None)
    args = p.parse_args()
    Calibrator().run(camera_index=args.camera)
