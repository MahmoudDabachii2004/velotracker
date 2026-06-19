"""VeloTracker - Camera module (macOS)."""

import cv2
import time
import config


class Camera:
    def __init__(self, camera_index=None, width=None, height=None):
        self._index = camera_index if camera_index is not None else config.CAMERA_INDEX
        self._width = width or config.CAMERA_WIDTH
        self._height = height or config.CAMERA_HEIGHT

        # On macOS, AVFoundation backend is the most reliable
        self._cap = cv2.VideoCapture(self._index, cv2.CAP_AVFOUNDATION)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Cannot open camera index {self._index}. "
                f"Is IriunWebcam running? Try --camera 1 or --camera 2."
            )

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)

        self._actual_width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._actual_height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self._frame_count = 0
        self._fps_start_time = time.time()
        self._current_fps = 0.0

        # Warmup (essential for IriunWebcam)
        print("[Camera] Warming up...")
        for _ in range(30):
            if self._cap.read()[0]:
                break
            time.sleep(0.1)

        print(f"[Camera] Open: index={self._index}, {self._actual_width}x{self._actual_height}")

    def read(self):
        success, frame = self._cap.read()
        if success:
            self._frame_count += 1
            elapsed = time.time() - self._fps_start_time
            if elapsed >= 1.0:
                self._current_fps = self._frame_count / elapsed
                self._frame_count = 0
                self._fps_start_time = time.time()
        return success, frame

    @property
    def fps(self):
        return self._current_fps

    def release(self):
        if self._cap and self._cap.isOpened():
            self._cap.release()
            print("[Camera] Released.")
