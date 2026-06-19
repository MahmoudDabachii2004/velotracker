import sys
import os
import numpy as np

# Add workspace directory to python path directly
sys.path.insert(0, r"c:\Users\newMahmoud\velotracker")

from modules.rpm_calculator import StickerKalmanFilter
from modules.ble_server import BLECadenceServer
import config

def test_kalman():
    print("--- Testing Kalman Filter ---")
    
    # Simulate moving in a circle with noise
    center = (320, 240)
    radius = 100.0
    dt = 1.0 / 30.0 # 30 FPS
    
    for q in [1000.0, 10000.0, 100000.0, 500000.0, 1000000.0]:
        kf = StickerKalmanFilter(q=q, r=9.0)
        np.random.seed(42)
        total_filt_err = 0.0
        total_noisy_err = 0.0
        
        for i in range(30):
            angle = i * 0.15
            true_x = center[0] + radius * np.cos(angle)
            true_y = center[1] + radius * np.sin(angle)
            
            # Add random noise
            noisy_x = true_x + np.random.normal(0, 3.0)
            noisy_y = true_y + np.random.normal(0, 3.0)
            
            filt_x, filt_y = kf.predict_and_update(noisy_x, noisy_y, i * dt)
            
            # Skip initial frames where filter is warming up
            if i >= 5:
                total_noisy_err += np.sqrt((noisy_x - true_x)**2 + (noisy_y - true_y)**2)
                total_filt_err += np.sqrt((filt_x - true_x)**2 + (filt_y - true_y)**2)
                
        print(f"q = {q:10.1f} | Avg Noisy Err: {total_noisy_err/25:.2f}px | Avg Filtered Err: {total_filt_err/25:.2f}px")

def test_zpower():
    print("\n--- Testing zPower Calculations ---")
    server = BLECadenceServer()
    
    # Test different RPM values for "fluid" trainer model
    config.POWER_MODEL = "fluid"
    for rpm in [0.0, 60.0, 90.0, 120.0]:
        watts = server._calculate_power(rpm)
        print(f"RPM: {rpm:3.1f} | zPower (fluid): {watts:3d} W")

    # Test different RPM values for "mag" trainer model
    config.POWER_MODEL = "mag"
    for rpm in [0.0, 60.0, 90.0, 120.0]:
        watts = server._calculate_power(rpm)
        print(f"RPM: {rpm:3.1f} | zPower (mag): {watts:3d} W")

if __name__ == "__main__":
    test_kalman()
    test_zpower()
