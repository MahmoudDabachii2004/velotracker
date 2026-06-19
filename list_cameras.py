"""VeloTracker - List available cameras (cross-platform).

Helps find the right CAMERA_INDEX (built-in webcam vs IriunWebcam).
"""

import cv2
import platform

# Select the best camera backend for the current platform
_PLATFORM = platform.system()

if _PLATFORM == "Darwin":
    _PREFERRED_BACKEND = cv2.CAP_AVFOUNDATION
elif _PLATFORM == "Windows":
    _PREFERRED_BACKEND = cv2.CAP_DSHOW
elif _PLATFORM == "Linux":
    _PREFERRED_BACKEND = cv2.CAP_V4L2
else:
    _PREFERRED_BACKEND = cv2.CAP_ANY


def list_cameras(max_index=5):
    print(f"Scanning cameras (index 0 to {max_index - 1})...")
    print()
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, _PREFERRED_BACKEND)
        if not cap.isOpened():
            cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if ok and frame is not None:
                print(f"  [OK]   Camera {i}: {w}x{h}")
                found.append(i)
            else:
                print(f"  [FAIL] Camera {i}: opens but no frames")
            cap.release()
        else:
            print(f"  [--]   Camera {i}: not available")
    print()
    if found:
        print(f"Working cameras: {found}")
        print(f"Edit config.py:  CAMERA_INDEX = {found[0]}")
    else:
        print("No working cameras. Make sure IriunWebcam is running on both devices.")


if __name__ == "__main__":
    list_cameras()

