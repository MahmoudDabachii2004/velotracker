"""
VeloTracker - BLE Server (macOS CoreBluetooth via bless)

Simulates a CSC (Cycling Speed and Cadence) sensor + FTMS controllable trainer.

Uses the GATT dict API from bless (the officially supported pattern).
This avoids the "Characteristics with cached values must be read-only" error
that occurs when using add_new_characteristic with an initial value on
notify/write characteristics.

Tested on:
  - macOS 12+ (Monterey, Ventura, Sonoma, Sequoia)
  - Python 3.9 - 3.13
  - bless 0.3+
"""

import asyncio
import struct
import time
import threading
from typing import Any, Optional, Dict
import platform

import config

if platform.system() != "Darwin":
    raise RuntimeError(
        "This BLE server is macOS-only. Use ble_server.py on Windows."
    )

from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions,
)

# ============================================================================
# GATT UUIDs
# ============================================================================
# CSC (Cycling Speed and Cadence)
CSC_SERVICE_UUID = "00001816-0000-1000-8000-00805f9b34fb"
CSC_MEASUREMENT_UUID = "00002a5b-0000-1000-8000-00805f9b34fb"
CSC_FEATURE_UUID = "00002a5c-0000-1000-8000-00805f9b34fb"

# FTMS (Fitness Machine Service)
FTMS_SERVICE_UUID = "00001826-0000-1000-8000-00805f9b34fb"
FTMS_FEATURE_UUID = "00002acc-0000-1000-8000-00805f9b34fb"
FTMS_INDOOR_BIKE_DATA_UUID = "00002ad2-0000-1000-8000-00805f9b34fb"
FTMS_CONTROL_POINT_UUID = "00002ad9-0000-1000-8000-00805f9b34fb"
FTMS_STATUS_UUID = "00002ada-0000-1000-8000-00805f9b34fb"


class BLECadenceServer:
    """BLE server: CSC + FTMS, via bless (CoreBluetooth) on macOS."""

    def __init__(self):
        self._server: Optional[BlessServer] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # CSC state
        self._cumulative_revolutions: int = 0
        self._last_event_time_1024: int = 0
        self._base_time: float = time.time()
        self._current_rpm: float = 0.0

        # Status
        self._status: str = "Off"
        self._client_connected: bool = False

    # ========================================================================
    # bless callbacks
    # ========================================================================
    def _read_request(self, characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
        return characteristic.value

    def _write_request(self, characteristic: BlessGATTCharacteristic, value: Any, **kwargs):
        characteristic.value = value

    # ========================================================================
    # Packet builders
    # ========================================================================
    def _build_csc_measurement(self) -> bytearray:
        """CSC Measurement: flags + wheel + crank."""
        flags = 0x03  # Wheel + Crank present
        wheel_revs = int(self._cumulative_revolutions * config.WHEEL_TO_CRANK_RATIO) & 0xFFFFFFFF
        wheel_time = self._last_event_time_1024 & 0xFFFF
        crank_revs = self._cumulative_revolutions & 0xFFFF
        crank_time = self._last_event_time_1024 & 0xFFFF
        return bytearray(struct.pack("<BIHHH", flags, wheel_revs, wheel_time, crank_revs, crank_time))

    def _build_indoor_bike_data(self) -> bytearray:
        """FTMS Indoor Bike Data: flags + speed + cadence + power."""
        flags = (1 << 2) | (1 << 6)  # cadence + power present
        # Speed in 0.01 km/h
        speed_kmh = (self._current_rpm * config.WHEEL_TO_CRANK_RATIO
                     * config.WHEEL_CIRCUMFERENCE_M * 60.0) / 100.0
        speed_raw = int(max(0, min(0xFFFF, speed_kmh)))
        # Cadence in 0.5 rpm units
        cadence_raw = int(max(0, min(0xFFFF, self._current_rpm * 2.0)))
        # Simplified power (W)
        power_w = int(max(0, min(0x7FFF, self._current_rpm * 0.8 + 30.0)))
        return bytearray(struct.pack("<HHHh", flags, speed_raw, cadence_raw, power_w))

    def _build_csc_feature(self) -> bytearray:
        return bytearray(struct.pack("<H", 0x0003))  # Wheel + Crank supported

    def _build_ftms_feature(self) -> bytearray:
        # bit 5 = Power measurement supported
        return bytearray(struct.pack("<QQQQ", (1 << 5), 0, 0, 0))

    # ========================================================================
    # Build the GATT dict (bless's officially supported API)
    # ========================================================================
    def _build_gatt_dict(self) -> Dict:
        """Build the GATT dict following bless's official pattern.

        Key: notify/write/indicate characteristics MUST have "Value": None.
        Read-only characteristics can have a static "Value" (bytearray).
        """
        csc_feature_value = self._build_csc_feature()
        ftms_feature_value = self._build_ftms_feature()

        gatt: Dict = {
            # ============== CSC Service ==============
            CSC_SERVICE_UUID: {
                # CSC Measurement: Notify + Read -> Value must be None
                CSC_MEASUREMENT_UUID: {
                    "Properties": (
                        GATTCharacteristicProperties.notify
                        | GATTCharacteristicProperties.read
                    ),
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": None,
                },
                # CSC Feature: Read-only -> can have a static Value
                CSC_FEATURE_UUID: {
                    "Properties": GATTCharacteristicProperties.read,
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": csc_feature_value,
                },
            },
            # ============== FTMS Service ==============
            FTMS_SERVICE_UUID: {
                # FTMS Feature: Read-only -> static Value
                FTMS_FEATURE_UUID: {
                    "Properties": GATTCharacteristicProperties.read,
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": ftms_feature_value,
                },
                # Indoor Bike Data: Notify + Read -> Value must be None
                FTMS_INDOOR_BIKE_DATA_UUID: {
                    "Properties": (
                        GATTCharacteristicProperties.notify
                        | GATTCharacteristicProperties.read
                    ),
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": None,
                },
                # Control Point: Write + Indicate -> Value must be None
                FTMS_CONTROL_POINT_UUID: {
                    "Properties": (
                        GATTCharacteristicProperties.write
                        | GATTCharacteristicProperties.indicate
                    ),
                    "Permissions": GATTAttributePermissions.writeable,
                    "Value": None,
                },
                # Status: Read + Notify -> Value must be None
                FTMS_STATUS_UUID: {
                    "Properties": (
                        GATTCharacteristicProperties.read
                        | GATTCharacteristicProperties.notify
                    ),
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": None,
                },
            },
        }
        return gatt

    # ========================================================================
    # Setup & run
    # ========================================================================
    async def _setup_and_run(self):
        try:
            print(f"[BLE] Creating BlessServer '{config.BLE_DEVICE_NAME}'...")
            self._server = BlessServer(name=config.BLE_DEVICE_NAME)
            self._server.read_request_func = self._read_request
            self._server.write_request_func = self._write_request

            # Register all services + characteristics at once via the GATT dict
            # This is bless's officially supported API and avoids the cached-value error.
            print("[BLE] Building GATT services (CSC + FTMS)...")
            gatt_dict = self._build_gatt_dict()
            await self._server.add_gatt(gatt_dict)

            print("[BLE] Starting advertising...")
            # CRITICAL: prioritize_local_name=False tells bless to broadcast
            # the service UUIDs in the advertisement, even though "VeloTracker"
            # is 11 chars (over the 10-char limit that triggers UUID dropping).
            # Without this, MyWhoosh can't find the device (it scans by FTMS UUID).
            await self._server.start(prioritize_local_name=False)
            self._status = "Advertising"
            print(f"[BLE] '{config.BLE_DEVICE_NAME}' is advertising.")
            print(f"[BLE] Open MyWhoosh -> Device Connection -> Controllable -> pair with '{config.BLE_DEVICE_NAME}'.")

            # Notification loop
            counter = 0
            while self._running:
                await asyncio.sleep(config.BLE_NOTIFY_INTERVAL_SEC)
                if not self._running:
                    break
                await self._send_notification()
                counter += 1
                if counter % 5 == 0:
                    try:
                        connected = await self._server.is_connected()
                        self._client_connected = bool(connected)
                    except Exception:
                        pass
                    print(f"[BLE] Connected: {self._client_connected} | "
                          f"Revs: {self._cumulative_revolutions} | "
                          f"RPM: {self._current_rpm:.1f}")

        except Exception as e:
            self._status = f"Error: {e}"
            print(f"[BLE] ERROR: {e}")
            import traceback
            traceback.print_exc()

    async def _send_notification(self):
        """Send CSC + FTMS notifications."""
        try:
            csc_value = self._build_csc_measurement()
            self._server.get_characteristic(CSC_MEASUREMENT_UUID).value = csc_value
            self._server.update_value(CSC_SERVICE_UUID, CSC_MEASUREMENT_UUID)

            bike_value = self._build_indoor_bike_data()
            self._server.get_characteristic(FTMS_INDOOR_BIKE_DATA_UUID).value = bike_value
            self._server.update_value(FTMS_SERVICE_UUID, FTMS_INDOOR_BIKE_DATA_UUID)
        except Exception as e:
            print(f"[BLE] Notification error: {e}")

    # ========================================================================
    # Public API
    # ========================================================================
    def update(self, total_revolutions: int,
               last_event_time: Optional[float] = None,
               current_rpm: float = 0.0):
        """Update CSC + FTMS data.

        IMPORTANT: We do NOT send immediate event-driven notifications.
        MyWhoosh computes RPM from the `last_event_time` field in the
        CSC Measurement packet (not from notification arrival time).
        Sending many notifications with the same rev count confuses
        MyWhoosh into thinking the rider stopped.

        We follow the PeloMon approach (proven open-source cycling BLE):
        - 500ms heartbeat sends notifications
        - Each notification carries the REAL last_event_time
        - MyWhoosh computes cadence = 60 * (delta_revs / delta_event_time)
        """
        self._current_rpm = current_rpm
        if total_revolutions != self._cumulative_revolutions:
            self._cumulative_revolutions = total_revolutions
            event_time = last_event_time if last_event_time is not None else time.time()
            elapsed = event_time - self._base_time
            self._last_event_time_1024 = int(elapsed * 1024) & 0xFFFF

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._setup_and_run())
        except RuntimeError as e:
            if "Event loop stopped" not in str(e):
                raise

    def start(self):
        self._running = True
        self._base_time = time.time()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print("[BLE] Background thread started.")

    def stop(self):
        self._running = False
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._status = "Off"
        print("[BLE] Server stopped.")

    @property
    def status(self) -> str:
        if self._client_connected:
            return "Connected"
        return self._status


if __name__ == "__main__":
    print("BLE test - simulating 75 RPM. Ctrl+C to stop.")
    server = BLECadenceServer()
    server.start()
    try:
        rev = 0
        while True:
            time.sleep(0.8)  # ~75 RPM
            rev += 1
            server.update(rev, current_rpm=75.0)
            print(f"  Rev: {rev}, Status: {server.status}")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        server.stop()
