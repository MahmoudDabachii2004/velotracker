import sys
import os
import numpy as np

# Add workspace directory to python path directly (cross-platform safe)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    # Use the @staticmethod so we don't need to instantiate the BLE server
    # (which would launch an asyncio loop and start advertising).
    
    # Test different RPM values for "fluid" trainer model (Kurt Kinetic)
    config.POWER_MODEL = "fluid"
    print(f"\n[fluid] Kurt Kinetic Road Machine (official curve)")
    for rpm in [0.0, 60.0, 80.0, 90.0, 100.0, 120.0]:
        watts = BLECadenceServer.calculate_power(rpm)
        speed_kmh = (rpm * config.WHEEL_TO_CRANK_RATIO * config.WHEEL_CIRCUMFERENCE_M * 60.0) / 1000.0
        print(f"  RPM: {rpm:3.1f} | speed: {speed_kmh:5.1f} km/h | zPower: {watts:3d} W")

    # Test different RPM values for "mag" trainer model
    config.POWER_MODEL = "mag"
    print(f"\n[mag] Generic magnetic trainer")
    for rpm in [0.0, 60.0, 80.0, 90.0, 100.0, 120.0]:
        watts = BLECadenceServer.calculate_power(rpm)
        speed_kmh = (rpm * config.WHEEL_TO_CRANK_RATIO * config.WHEEL_CIRCUMFERENCE_M * 60.0) / 1000.0
        print(f"  RPM: {rpm:3.1f} | speed: {speed_kmh:5.1f} km/h | zPower: {watts:3d} W")

    # Test linear model (debug only)
    config.POWER_MODEL = "linear"
    print(f"\n[linear] Debug mode (not accurate to any real trainer)")
    for rpm in [0.0, 60.0, 80.0, 90.0, 100.0, 120.0]:
        watts = BLECadenceServer.calculate_power(rpm)
        print(f"  RPM: {rpm:3.1f} | zPower: {watts:3d} W")

if __name__ == "__main__":
    test_kalman()
    test_zpower()
