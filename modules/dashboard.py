"""VeloTracker - Dashboard module."""

import cv2
import numpy as np
import config


class Dashboard:
    def __init__(self):
        self._font = cv2.FONT_HERSHEY_SIMPLEX

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

        if debug_mask and detection is not None:
            mask_small = cv2.resize(detection.mask, (w // 4, h // 4))
            mask_bgr = cv2.cvtColor(mask_small, cv2.COLOR_GRAY2BGR)
            yo = h - h // 4 - 30
            xo = w - w // 4 - 10
            display[yo:yo + h // 4, xo:xo + w // 4] = mask_bgr

        return display

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
