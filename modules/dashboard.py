"""VeloTracker - Dashboard module."""

import cv2
import numpy as np
from collections import deque
import config


class Dashboard:
    def __init__(self):
        self._font = cv2.FONT_HERSHEY_SIMPLEX
        self._omega_history = deque(maxlen=100)

    def draw(self, frame, detection, rpm_calc, ble_status="Off", fps=0.0, debug_mask=False):
        display = frame.copy()
        h, w = display.shape[:2]

        self._draw_tracking(display, detection, rpm_calc)

        # Top overlay
        overlay = display.copy()
        cv2.rectangle(overlay, (0, 0), (w, 80), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, display, 0.4, 0, display)

        # RPM text
        phase = rpm_calc.phase
        rpm_value = rpm_calc.rpm
        if phase == "TRACKING" and rpm_value > 0:
            if rpm_calc.is_predicted:
                color = config.COLOR_YELLOW
                rpm_text = f"RPM: {rpm_value:.0f} (PRED)"
            else:
                color = config.COLOR_GREEN
                rpm_text = f"RPM: {rpm_value:.0f}"
        elif phase == "TRACKING" and rpm_value == 0:
            color = config.COLOR_RED
            rpm_text = "RPM: ---"
        elif phase == "CALIBRATING":
            color = config.COLOR_YELLOW
            rpm_text = f"CALIBRATION {rpm_calc.calibration_progress * 100:.0f}%"
        else:
            color = config.COLOR_RED
            rpm_text = "WAITING..."

        cv2.putText(display, rpm_text, (15, 50),
                    self._font, config.FONT_SCALE_LARGE, color, 3)

        if phase == "TRACKING":
            cv2.putText(display, f"Revolutions: {rpm_calc.revolutions}",
                        (15, 72), self._font, config.FONT_SCALE_SMALL,
                        config.COLOR_WHITE, 1)

        # BLE status
        ble_color = config.COLOR_GREEN if ("Connected" in ble_status or "Advertising" in ble_status) else config.COLOR_RED
        ble_text = f"BLE: {ble_status}"
        ts = cv2.getTextSize(ble_text, self._font, config.FONT_SCALE_MEDIUM, 1)[0]
        cv2.putText(display, ble_text, (w - ts[0] - 15, 30),
                    self._font, config.FONT_SCALE_MEDIUM, ble_color, 2)

        # FPS
        fps_text = f"FPS: {fps:.0f}"
        ts = cv2.getTextSize(fps_text, self._font, config.FONT_SCALE_SMALL, 1)[0]
        cv2.putText(display, fps_text, (w - ts[0] - 10, h - 10),
                    self._font, config.FONT_SCALE_SMALL, config.COLOR_WHITE, 1)

        # Help
        cv2.putText(display, "Q:Quit  C:Recalibrate  R:Reset",
                    (10, h - 10), self._font, config.FONT_SCALE_SMALL,
                    (150, 150, 150), 1)

        # Display detection confidence
        if detection is not None:
            conf = getattr(detection, "detection_confidence", 0.0)
            cv2.putText(display, f"Conf: {conf * 100:.0f}%", (15, h - 35),
                        self._font, config.FONT_SCALE_SMALL, config.COLOR_GREEN if conf > 0.7 else config.COLOR_YELLOW, 1)

        # Debug overlays
        if debug_mask:
            # Stats overlay
            raw_rpm = rpm_calc.raw_rpm
            smoothed_rpm = rpm_calc.rpm
            omega = rpm_calc.omega
            alpha = rpm_calc.alpha
            debug_text_1 = f"Raw RPM: {raw_rpm:.1f} | Smooth: {smoothed_rpm:.1f}"
            debug_text_2 = f"Omega: {omega:.2f} | Alpha: {alpha:.2f}"
            cv2.putText(display, debug_text_1, (w - 280, 52),
                        self._font, config.FONT_SCALE_SMALL, config.COLOR_WHITE, 1)
            cv2.putText(display, debug_text_2, (w - 280, 68),
                        self._font, config.FONT_SCALE_SMALL, config.COLOR_WHITE, 1)

            # Append to omega history for mini-graph
            self._omega_history.append(omega)
            graph_w, graph_h = 120, 60
            yo_graph = h - graph_h - 30
            xo_graph = w - graph_w - 10

            # Resize and show mask
            if detection is not None:
                mask_small = cv2.resize(detection.mask, (w // 4, h // 4))
                mask_bgr = cv2.cvtColor(mask_small, cv2.COLOR_GRAY2BGR)
                yo = h - h // 4 - 30
                xo = w - w // 4 - 10
                display[yo:yo + h // 4, xo:xo + w // 4] = mask_bgr
                xo_graph = w - (w // 4) - graph_w - 20

            # Draw mini-graph
            self._draw_mini_graph(display, xo_graph, yo_graph, graph_w, graph_h, self._omega_history)

        return display

    def _draw_mini_graph(self, img, x_offset, y_offset, width, height, value_history):
        if not value_history:
            return
        # Draw background container
        cv2.rectangle(img, (x_offset, y_offset), (x_offset + width, y_offset + height), (30, 30, 30), -1)
        cv2.rectangle(img, (x_offset, y_offset), (x_offset + width, y_offset + height), (70, 70, 70), 1)

        # Scale values to fit graph height
        vals = list(value_history)
        min_v = -3.5
        max_v = 3.5
        if len(vals) > 1:
            actual_min = min(vals)
            actual_max = max(vals)
            if actual_min >= 0:
                min_v = 0.0
                max_v = max(1.0, actual_max * 1.2)
            else:
                bound = max(abs(actual_min), abs(actual_max))
                min_v = -max(1.0, bound)
                max_v = max(1.0, bound)

        h_range = max_v - min_v
        if h_range == 0:
            h_range = 1.0

        pts = []
        step = width / (len(vals) - 1) if len(vals) > 1 else width
        for i, val in enumerate(vals):
            px = int(x_offset + i * step)
            py = int(y_offset + height - ((val - min_v) / h_range) * height)
            py = max(y_offset, min(y_offset + height, py))
            pts.append((px, py))

        # Draw lines
        for i in range(1, len(pts)):
            cv2.line(img, pts[i - 1], pts[i], config.COLOR_CYAN, 1)

        # Label min/max
        cv2.putText(img, f"{max_v:.1f}", (x_offset + 2, y_offset + 10),
                    self._font, 0.35, config.COLOR_WHITE, 1)
        cv2.putText(img, f"{min_v:.1f}", (x_offset + 2, y_offset + height - 3),
                    self._font, 0.35, config.COLOR_WHITE, 1)
        # Small axis label
        cv2.putText(img, "omega", (x_offset + width - 35, y_offset + 10),
                    self._font, 0.3, (150, 150, 150), 1)

    def _draw_tracking(self, frame, detection, rpm_calc):
        center = rpm_calc.center
        if center is not None:
            cx, cy = int(center[0]), int(center[1])
            cv2.line(frame, (cx - 12, cy), (cx + 12, cy), config.COLOR_RED, 2)
            cv2.line(frame, (cx, cy - 12), (cx, cy + 12), config.COLOR_RED, 2)
            r = int(rpm_calc.radius)
            if r > 0:
                cv2.circle(frame, (cx, cy), r, config.COLOR_RED, 1)

        trail = rpm_calc.trail
        if len(trail) > 1:
            for i in range(1, len(trail)):
                a = i / len(trail)
                color = (int(config.COLOR_CYAN[0] * a),
                         int(config.COLOR_CYAN[1] * a),
                         int(config.COLOR_CYAN[2] * a))
                cv2.line(frame, trail[i - 1], trail[i], color, max(1, int(2 * a)))

        if detection is not None:
            if detection.contour is not None:
                cv2.drawContours(frame, [detection.contour], -1, config.COLOR_GREEN, 2)
            cv2.circle(frame, (detection.cx, detection.cy), 6, config.COLOR_BLUE, -1)
            cv2.circle(frame, (detection.cx, detection.cy), 8, config.COLOR_WHITE, 2)
        elif rpm_calc.phase == "TRACKING" and rpm_calc.is_predicted and rpm_calc.predicted_position:
            px, py = int(round(rpm_calc.predicted_position[0])), int(round(rpm_calc.predicted_position[1]))
            cv2.circle(frame, (px, py), 6, config.COLOR_YELLOW, -1)
            cv2.circle(frame, (px, py), 8, config.COLOR_WHITE, 2)
            cv2.putText(frame, "PRED", (px + 12, py + 5),
                        self._font, 0.4, config.COLOR_YELLOW, 1)
