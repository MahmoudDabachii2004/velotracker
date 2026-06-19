# VeloTracker (macOS)

Turn any old stationary bike into a smart trainer for **MyWhoosh** — for **$0**.

VeloTracker uses your iPhone (as a webcam via IriunWebcam) pointed at the pedal, detects a colored sticker via OpenCV, computes your RPM, and broadcasts cadence + speed + power over Bluetooth Low Energy (BLE) using macOS's native CoreBluetooth. MyWhoosh sees it as a real Wahoo/Garmin-style sensor.

This version uses **bless + CoreBluetooth** on macOS — rock-solid BLE peripheral mode, no driver issues.

---

## Architecture

```
[iPhone + IriunWebcam]  (pointed at pedal)
            |
            v
[MacBook running VeloTracker]
    |-- OpenCV: HSV color detection finds sticker
    |-- Kasa circle fit + OLS regression -> RPM
    |-- bless + CoreBluetooth BLE server:
    |       * CSC Service  (0x1816) -> cadence + speed
    |       * FTMS Service (0x1826) -> controllable trainer
            |
            | (BLE radio, ~10m range)
            v
[MyWhoosh pairs with "VeloTracker"]
```

MyWhoosh runs on the **same MacBook** — it pairs with the local BLE server.

---

## Prerequisites

- **MacBook** (Intel or Apple Silicon M1/M2/M3)
- **macOS 12+** (Monterey or newer)
- **Python 3.9 - 3.13** (3.14 is not yet supported by bless)
- **iPhone** with IriunWebcam (free from App Store)
- A bright colored sticker (green/orange/pink/yellow)
- A way to mount the iPhone to see the pedal

---

## Setup

### 1. Install IriunWebcam
1. Download IriunWebcam from https://iriun.com/ on your Mac and iPhone.
2. Launch on both devices. They auto-connect over Wi-Fi.
3. Your iPhone camera now appears as a webcam on the Mac.

### 2. Install Python (if not already)
- **Easiest:** Install Homebrew first (https://brew.sh), then:
  ```
  brew install python@3.13
  ```
- Or download from https://www.python.org/downloads/macos/

Check it works:
```
python3 --version
```

### 3. Install VeloTracker
1. Extract this folder to e.g. `~/VeloTracker`.
2. Open Terminal in that folder.
3. Run:
  ```
  pip3 install -r requirements.txt
  ```

### 4. Find your camera index
```
python3 list_cameras.py
```
You'll see something like:
```
[OK]   Camera 0: 1280x720
[OK]   Camera 1: 1920x1080
```
The higher-resolution one is usually IriunWebcam (your iPhone). Note the number.

### 5. Set camera index in config.py
Open `config.py` in TextEdit. Find:
```python
CAMERA_INDEX = 0
```
Change to the index you found in step 4 (e.g. `CAMERA_INDEX = 1`).

### 6. Calibrate your sticker
1. Stick a bright colored sticker (green/orange/pink) on the pedal.
2. Mount iPhone to see the pedal making a full circle.
3. Run:
  ```
  python3 calibrate.py
  ```
4. In the calibration window, **click directly on the sticker**.
5. Green contour should wrap around the sticker.
6. Press **S** to save, **Q** to quit.

---

## macOS Bluetooth permission

The first time you run VeloTracker, macOS will pop up a dialog asking for **Bluetooth permission** for Terminal (or your Python interpreter). Click **OK**. Without this, the BLE server won't start.

If you accidentally clicked "Don't Allow", go to:
**System Settings → Privacy & Security → Bluetooth** → enable your Terminal/Python.

---

## Testing

### Step A — Test BLE alone (no camera)

```
python3 ble_diagnostic.py
```

You should see:
```
[BLE] Background thread started.
[BLE] Creating BlessServer 'VeloTracker'...
[BLE] Adding CSC service (0x1816)...
[BLE] Adding FTMS service (0x1826)...
[BLE] Starting advertising...
[BLE] 'VeloTracker' is advertising.
[BLE] Open MyWhoosh -> Device Connection -> Controllable -> pair with 'VeloTracker'.
```

Open MyWhoosh → Device Connection → tap **Controllable** → pair with "VeloTracker".
You should see ~9.5 km/h, ~75 RPM, ~90 W.

### Step B — Full pipeline

```
python3 main.py
```

Pedal for ~3 seconds (calibration). You should see `RPM: XX` in green.
In MyWhoosh, speed should respond to your pedaling.

**Keys:**
- `Q` = Quit
- `C` = Recalibrate center (if bike moved)
- `R` = Full reset

### Step C — Pair with MyWhoosh
1. Open MyWhoosh.
2. Device Connection → tap **Controllable** → pair with "VeloTracker".
3. Hit **Ride!**

---

## Troubleshooting

### "Bluetooth permission denied"
macOS blocked Python's access to Bluetooth. Go to:
**System Settings → Privacy & Security → Bluetooth** → enable your Terminal/Python.

### "MyWhoosh detects VeloTracker but speed/power stay at 0"
The camera isn't detecting the sticker. Run `python3 main.py` and check the dashboard.
Does "RPM: XX" show > 0 when pedaling?
- If not: re-run `python3 calibrate.py`, click directly on the sticker.
- Make sure the sticker is well-lit.
- Make sure the sticker is the only thing in frame that's that color.

### "Camera opens but shows black image"
- IriunWebcam not running? Launch it on both Mac and iPhone.
- iPhone and Mac on different Wi-Fi? Connect both to the same network.

### "Camera index 0 doesn't work"
Try `python3 main.py --camera 1` or `--camera 2`.

### "RPM number jumps wildly"
Press `C` to recalibrate. Pedal steadily for ~3 seconds. Make sure:
- Camera is stable
- Pedal makes a clear circle in frame (not an ellipse from a side angle)
- Sticker is visible at all positions of the pedal stroke

### "BLE works but MyWhoosh doesn't see it"
- Make sure MyWhoosh has Bluetooth permission too (System Settings → Privacy & Security → Bluetooth).
- Toggle Bluetooth off and on in System Settings.
- Restart MyWhoosh.

---

## File structure

```
VeloTracker/
  config.py              Settings (camera index, HSV color, BLE name)
  main.py                Main entry point
  calibrate.py           Sticker color calibration
  ble_diagnostic.py      Standalone BLE test
  list_cameras.py        Find the right camera index
  requirements.txt       Python dependencies
  README.md              This file
  modules/
    __init__.py
    camera.py            OpenCV camera wrapper (AVFoundation backend)
    detector.py          HSV color detection
    rpm_calculator.py    Kasa circle fit + OLS RPM regression
    dashboard.py         OpenCV HUD overlay
    ble_server.py        bless + CoreBluetooth BLE GATT server
```

---

## How RPM is computed

1. **HSV color detection** finds the sticker's centroid each frame.
2. **Light EMA smoothing** reduces jitter (~8ms latency).
3. **Kasa circle fit** (first 90 frames): algebraic fit finds rotation center + radius. CV < 0.5 required.
4. **Continuous center adjustment**: circle is re-fit on last 150 positions.
5. **Phase unwrapping**: `atan2(dy, dx)` unwrapped across +/- pi gives continuous total angle.
6. **OLS regression**: 20-frame sliding window fits (time, total_angle). Slope = angular velocity = RPM.
7. **Glitch rejection**: angle delta > 1 rad/frame is rejected.
8. **Circular predictor**: if detection lost up to 3 sec, predicts position from current RPM and continues counting.

---

## License
Free for personal use. Have fun riding!
