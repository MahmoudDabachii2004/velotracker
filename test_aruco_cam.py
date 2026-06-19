"""
VeloTracker - 3D AruCo Marker Tracking Test Script
Run this script to verify if your hand-drawn AruCo marker is correctly detected in 3D.
"""

import cv2
import numpy as np
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import config

def draw_axis(img, rvec, tvec, K, D, length):
    """Draw 3D axis on the image (Red = X, Green = Y, Blue = Z)."""
    if hasattr(cv2, "drawFrameAxes"):
        cv2.drawFrameAxes(img, K, D, rvec, tvec, length)
    elif hasattr(cv2.aruco, "drawAxis"):
        cv2.aruco.drawAxis(img, K, D, rvec, tvec, length)
    else:
        # Fallback manual projection
        axis_pts = np.array([[0,0,0], [length,0,0], [0,length,0], [0,0,length]], dtype=np.float32)
        img_pts, _ = cv2.projectPoints(axis_pts, rvec, tvec, K, D)
        img_pts = img_pts.reshape(-1, 2).astype(int)
        # X-axis (Red)
        cv2.line(img, tuple(img_pts[0]), tuple(img_pts[1]), (0, 0, 255), 3)
        # Y-axis (Green)
        cv2.line(img, tuple(img_pts[0]), tuple(img_pts[2]), (0, 255, 0), 3)
        # Z-axis (Blue)
        cv2.line(img, tuple(img_pts[0]), tuple(img_pts[3]), (255, 0, 0), 3)

def main():
    print("======================================================")
    print("VeloTracker - AruCo 3D Pose Detection Test")
    print("======================================================")
    print("Instructions:")
    print("  1. Present your hand-drawn marker (DICT_4X4_50 ID 0) to the camera.")
    print("  2. If detected, a 3D axis (RGB) will align to the marker.")
    print("  3. Press 'q' on the camera window to exit.")
    print("======================================================")

    # Initialize Camera
    camera_index = getattr(config, "CAMERA_INDEX", 0)
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"Error: Could not open camera with index {camera_index}")
        return

    # Set frame size
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # Get AruCo dictionary and parameters
    try:
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    except AttributeError:
        dictionary = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)

    try:
        parameters = cv2.aruco.DetectorParameters()
    except AttributeError:
        parameters = cv2.aruco.DetectorParameters_create()

    # Define physical marker length (3.6 cm = 0.036 meters)
    marker_length = 0.036 

    # Define object points of the square marker in its own 3D frame
    half_l = marker_length / 2.0
    obj_points = np.array([
        [-half_l,  half_l, 0.0],  # Top-Left
        [ half_l,  half_l, 0.0],  # Top-Right
        [ half_l, -half_l, 0.0],  # Bottom-Right
        [-half_l, -half_l, 0.0]   # Bottom-Left
    ], dtype=np.float32)

    # Generate heuristic Camera Matrix (K) and Distortion coefficients (D)
    # Since we don't have calibration values, we approximate based on 640x480 resolution
    K = np.array([
        [640.0,   0.0, 320.0],
        [  0.0, 640.0, 240.0],
        [  0.0,   0.0,   1.0]
    ], dtype=np.float32)
    D = np.zeros((5, 1), dtype=np.float32) # Assume zero lens distortion

    print("Camera started. Press 'q' to quit.")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame.")
            break

        # Detect markers
        if hasattr(cv2.aruco, "ArucoDetector"):
            detector = cv2.aruco.ArucoDetector(dictionary, parameters)
            corners, ids, _ = detector.detectMarkers(frame)
        else:
            corners, ids, _ = cv2.aruco.detectMarkers(frame, dictionary, parameters=parameters)

        if ids is not None:
            # Draw contours around detected markers
            cv2.aruco.drawDetectedMarkers(frame, corners, ids)

            for i in range(len(ids)):
                marker_id = ids[i][0]
                marker_corners = corners[i][0]

                # Estimate 3D Pose using solvePnP
                success, rvec, tvec = cv2.solvePnP(obj_points, marker_corners, K, D)

                if success:
                    # Draw 3D coordinate axis on the marker
                    draw_axis(frame, rvec, tvec, K, D, marker_length)

                    # Calculate distance in centimeters (Z axis represents distance forward from camera)
                    distance_cm = tvec[2][0] * 100.0

                    # Display ID and distance on screen
                    x = int(marker_corners[0][0])
                    y = int(marker_corners[0][1])
                    cv2.putText(
                        frame,
                        f"ID: {marker_id} Dist: {distance_cm:.1f}cm",
                        (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 0),
                        2
                    )

        # Show output window
        cv2.imshow("VeloTracker - 3D AruCo Test", frame)

        # Exit on 'q'
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("Camera closed. Test script finished.")

if __name__ == "__main__":
    main()
