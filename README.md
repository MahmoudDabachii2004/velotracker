# VeloTracker

Turn any old stationary bike into a smart trainer for **MyWhoosh** — for **$0**.

VeloTracker uses your iPhone (as a webcam via IriunWebcam) pointed at the pedal, detects a colored sticker via OpenCV, computes your RPM, and broadcasts cadence + speed + power over Bluetooth Low Energy (BLE) using the **bless** library. MyWhoosh sees it as a real Wahoo/Garmin-style sensor.

**Cross-platform:** Works on macOS, Windows, and Linux.

---

## Architecture

```
[iPhone + IriunWebcam]  (pointed at pedal)
            |
            v
[Computer running VeloTracker]
    |-- OpenCV: HSV color detection finds sticker
    |-- Taubin circle fit + weighted OLS regression -> RPM
    |-- bless BLE server:
    |       * CSC Service  (0x1816) -> cadence + speed
    |       * FTMS Service (0x1826) -> controllable trainer
            |
            | (BLE radio, ~10m range)
            v
[MyWhoosh pairs with "VeloTracker"]
```

MyWhoosh runs on the **same computer** — it pairs with the local BLE server.

---

## Prerequisites

### All Platforms
- **iPhone** with IriunWebcam (free from App Store)
- A bright colored sticker (green/orange/pink/yellow)
- A way to mount the iPhone to see the pedal

### macOS
- **MacBook** (Intel or Apple Silicon M1/M2/M3)
- **macOS 12+** (Monterey or newer)
- **Python 3.13.14** (Recommended) or any 3.9 - 3.13 version

### Windows
- **Windows 10+** with a Bluetooth adapter that supports **BLE peripheral mode**
  (most built-in laptop Bluetooth works; some cheap USB dongles are central-only)
- **Python 3.12.10** (Recommended) (Avoid Python 3.13 on Windows, as its BLE WinRT dependencies lack 3.13 wheels)

### Linux
- **BlueZ** 5.43+ with D-Bus
- **Python 3.9+**

---

## Setup

### 1. Install IriunWebcam
1. Download IriunWebcam from https://iriun.com/ on your computer and iPhone.
2. Launch on both devices. They auto-connect over Wi-Fi.
3. Your iPhone camera now appears as a webcam on the computer.

### 2. Install Python

#### **macOS**
* **Optimal Python Version:** **Python 3.13.14** (Works with 3.9 - 3.13)
* **Method A: Official Installer (Recommended)**
  1. Go to the [Python 3.13.14 Release Page](https://www.python.org/downloads/release/python-31314/).
  2. Download and run the **macOS 64-bit universal2 installer**.
  3. **Important post-install step:** Open `/Applications/Python 3.13` in Finder and double-click `Install Certificates.command`. This ensures SSL connections work properly.
* **Method B: Homebrew**
  If you have Homebrew installed, open a terminal and run:
  ```bash
  brew install python@3.13
  ```

#### **Windows**
* **Optimal Python Version:** **Python 3.12.10** (Do **NOT** use Python 3.13 on Windows, as its BLE WinRT dependencies lack 3.13 wheels. 3.12.10 is the latest 3.12 release that provides an official `.exe` binary installer; newer 3.12 releases are source-only).
* **Method: Official Installer**
  1. Go to the [Python 3.12.10 Release Page](https://www.python.org/downloads/release/python-31210/).
  2. Download and run the **Windows installer (64-bit)**.
  3. **CRITICAL STEP:** Before clicking "Install Now", make sure to check the box at the bottom that says **"Add python.exe to PATH"**.
  4. Follow the installer instructions to finish.

#### **Linux**
* **Recommended Python Version:** **Python 3.9+**
* Open your terminal and install via your package manager (e.g. Debian/Ubuntu):
  ```bash
  sudo apt update
  sudo apt install python3 python3-pip python3-venv
  ```

---

#### **Verify Installation**
Open your terminal (on macOS/Linux) or Command Prompt/PowerShell (on Windows) and verify the version:
```bash
# On macOS / Linux:
python3 --version

# On Windows:
python --version
```
Ensure the version printed matches the guidelines above.

### 3. Install VeloTracker
1. Extract this folder (or `git clone` the repo).
2. Open a terminal in that folder.
3. Run:
  ```
  pip install -r requirements.txt
  ```

**Windows only** — install the additional BLE dependency:
```
pip install git+https://github.com/gwangyi/pysetupdi
```

### 4. Find your camera index
```
python3 list_cameras.py       # macOS/Linux
python list_cameras.py        # Windows
```
You'll see something like:
```
[OK]   Camera 0: 1280x720
[OK]   Camera 1: 1920x1080
```
The higher-resolution one is usually IriunWebcam (your iPhone). Note the number.

### 5. Set camera index in config.py
Open `config.py` in a text editor. Find:
```python
CAMERA_INDEX = 0
```
Change to the index you found in step 4 (e.g. `CAMERA_INDEX = 1`).

### 6. Tuning Advanced parameters (Optional)
You can customize these parameters in `config.py`:
* **CLAHE_CLIP_LIMIT** (Default `3.0`): Local contrast enhancement limit. Higher values increase contrast but can add noise.
* **RPM_DECAY_FACTOR** (Default `0.85`): Per-frame decay rate of RPM when prediction/detection is lost.
* **RPM_EMA_ALPHA** (Default `0.15`): Dashboard RPM smoothing factor.
* **RPM_WEIGHTED_OLS_LAMBDA** (Default `2.0`): Temporal weight decay factor for RPM estimation. Higher values make the system respond faster to speed changes.

### 6. Calibrate your sticker
1. Stick a bright colored sticker (green/orange/pink) on the pedal.
2. Mount iPhone to see the pedal making a full circle.
3. Run:
  ```
  python3 calibrate.py         # macOS/Linux
  python calibrate.py          # Windows
  ```
4. In the calibration window, **click directly on the sticker**.
5. Green contour should wrap around the sticker.
6. Press **S** to save, **Q** to quit.

---

## Bluetooth Permission

### macOS
The first time you run VeloTracker, macOS will pop up a dialog asking for **Bluetooth permission** for Terminal (or your Python interpreter). Click **OK**. Without this, the BLE server won't start.

If you accidentally clicked "Don't Allow", go to:
**System Settings → Privacy & Security → Bluetooth** → enable your Terminal/Python.

### Windows
Make sure Bluetooth is enabled in **Settings → Bluetooth & devices**. Your Bluetooth adapter must support BLE **peripheral** (advertising) mode. Most laptop adapters do; some USB dongles don't.

### Linux
Your user must be in the `bluetooth` group, and BlueZ must be running:
```
sudo usermod -a -G bluetooth $USER
sudo systemctl enable --now bluetooth
```

---

## Testing

### Step A — Test BLE alone (no camera)

```
python3 ble_diagnostic.py      # macOS/Linux
python ble_diagnostic.py       # Windows
```

You should see:
```
[BLE] Background thread started.
[BLE] Creating BlessServer 'V'...
[BLE] Adding CSC service (0x1816)...
[BLE] Adding FTMS service (0x1826)...
[BLE] Starting advertising...
[BLE] 'V' is advertising.
[BLE] Open MyWhoosh -> Device Connection -> Controllable -> pair with 'V'.
```

Open MyWhoosh → Device Connection → tap **Controllable** → pair with "V".
You should see ~9.5 km/h, ~75 RPM, ~90 W.

### Step B — Full pipeline

```
python3 main.py                # macOS/Linux
python main.py                 # Windows
```

Pedal for ~3 seconds (calibration). You should see `RPM: XX` in green.
In MyWhoosh, speed should respond to your pedaling.

**Keys:**
- `Q` = Quit
- `C` = Recalibrate center (if bike moved)
- `R` = Full reset

### Step C — Pair with MyWhoosh
1. Open MyWhoosh.
2. Device Connection → tap **Controllable** → pair with "VeloTrack".
3. Hit **Ride!**

---

## Troubleshooting

### "Bluetooth permission denied" (macOS)
macOS blocked Python's access to Bluetooth. Go to:
**System Settings → Privacy & Security → Bluetooth** → enable your Terminal/Python.

### "No module named pysetupdi" (Windows)
You need to install pysetupdi manually:
```
pip install git+https://github.com/gwangyi/pysetupdi
```

### "winrt-* package not found / build error" (Windows)
You're probably using Python 3.13. **Use Python 3.11 or 3.12 instead** — bless's WinRT dependencies (`winrt-*==2.0.0b1`) don't have Python 3.13 wheels.

### "MyWhoosh detects VeloTracker but speed/power stay at 0"
The camera isn't detecting the sticker. Run `python3 main.py` and check the dashboard.
Does "RPM: XX" show > 0 when pedaling?
- If not: re-run `python3 calibrate.py`, click directly on the sticker.
- Make sure the sticker is well-lit.
- Make sure the sticker is the only thing in frame that's that color.

### "Camera opens but shows black image"
- IriunWebcam not running? Launch it on both computer and iPhone.
- iPhone and computer on different Wi-Fi? Connect both to the same network.

### "Camera index 0 doesn't work"
Try `python3 main.py --camera 1` or `--camera 2`.

### "RPM number jumps wildly"
Press `C` to recalibrate. Pedal steadily for ~3 seconds. Make sure:
- Camera is stable
- Pedal makes a clear circle in frame (not an ellipse from a side angle)
- Sticker is visible at all positions of the pedal stroke

### "BLE works but MyWhoosh doesn't see it"
- Make sure MyWhoosh has Bluetooth permission too.
- Toggle Bluetooth off and on.
- Restart MyWhoosh.
- **Windows:** Make sure your Bluetooth adapter supports BLE peripheral mode.

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
    camera.py            OpenCV camera wrapper (platform-aware backend)
    detector.py          HSV color detection
    rpm_calculator.py    Taubin circle fit + weighted OLS RPM regression
    dashboard.py         OpenCV HUD overlay
    ble_server.py        bless BLE GATT server (cross-platform)
```

---

## How RPM is computed

1. **Bilateral filtering** smooths color noise while preserving sticker edges.
2. **CLAHE local contrast normalization** on the Value (brightness) channel makes detection highly robust to shadows and changing lighting.
3. **HSV color detection** finds the sticker's centroid each frame (with CLAHE on the V channel for lighting invariance).
4. **Kalman filter (4D state: x, y, vx, vy)** reduces measurement jitter on the centroid.
5. **Taubin algebraic circle fit** (first 90 frames): finds rotation center + radius. Taubin is much more stable than Kasa for partial arcs (short calibration times).
6. **Continuous center adjustment**: circle is re-fit on last 150 positions.
7. **Phase unwrapping**: `atan2(dy, dx)` unwrapped across +/- pi gives continuous total angle.
8. **Weighted OLS regression**: 20-frame sliding window fits (time, total_angle) using exponential decay weights (recent samples count more). This responds faster to accelerations/decelerations.
9. **Glitch rejection**: angle delta > 0.3 rad/frame (adaptive: scaled by dt and predicted omega) is rejected. At 30 FPS this is ~86 RPM; human cadence stays comfortably below.
10. **Circular predictor with acceleration**: if detection is lost, predicts position using the last known velocity and angular acceleration (linear extrapolation), decaying naturally to a stop, and counting revolutions for up to 3 seconds.

---

## License
Free for personal use. Have fun riding!
