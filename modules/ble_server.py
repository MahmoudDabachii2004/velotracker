"""
VeloTracker - BLE Server (cross-platform via bless)

Simulates a CSC (Cycling Speed and Cadence) sensor + FTMS controllable trainer.

Uses the GATT dict API from bless (the officially supported pattern).
This avoids the "Characteristics with cached values must be read-only" error
that occurs when using add_new_characteristic with an initial value on
notify/write characteristics.

Platform backends (handled automatically by bless):
  - macOS:   CoreBluetooth
  - Windows: WinRT (requires pywin32, winrt-* packages, pysetupdi)
  - Linux:   BlueZ via D-Bus

Tested on:
  - macOS 12+ (Monterey, Ventura, Sonoma, Sequoia) — Python 3.9-3.13
  - Windows 10+ — Python 3.11-3.12 (winrt-* 2.0.0b1 lacks 3.13 wheels)
  - bless 0.3+
"""

import asyncio
import struct
import time
import threading
import platform
from typing import Any, Optional, Dict

import config

# ============================================================================
# Windows BLEAdapter Monkeypatch (Fixes MediaTek/Windows 11 CreateFile Error 2)
# ============================================================================
if platform.system() == "Windows":
    try:
        import bless.backends.winrt.ble.adapter as bless_adapter
        import win32file
        from win32con import GENERIC_WRITE, OPEN_EXISTING

        orig_init = bless_adapter.BLEAdapter.__init__

        def patched_init(self, *args, **kwargs):
            # Try multiple GUIDs to find the correct Bluetooth radio state interface:
            # 1. `{a5dcbf10-6530-11d2-901f-00c04fb951ed}` (Default bless GUID for USB)
            # 2. `{92383b0e-f90e-4ac9-8d44-8c2d0d0ebda2}` (Bluetooth Radio State GUID, fixes MediaTek/Windows 11)
            # 3. `{2f5812b3-6f6f-4bb6-ad79-ad4771f9c1e6}` (Alternative Bluetooth GUID)
            guids = [
                "{a5dcbf10-6530-11d2-901f-00c04fb951ed}",
                "{92383b0e-f90e-4ac9-8d44-8c2d0d0ebda2}",
                "{2f5812b3-6f6f-4bb6-ad79-ad4771f9c1e6}"
            ]

            get_adapter_func = getattr(bless_adapter, "get_bluetooth_adapter", None)
            if get_adapter_func is None:
                try:
                    from pysetupdi import get_bluetooth_adapter as get_adapter_func
                except ImportError:
                    pass

            if get_adapter_func is not None:
                self._adapter_name = get_adapter_func()
            else:
                self._adapter_name = "get_bluetooth_adapter_not_found"

            self._device_name = self._adapter_name.replace("\\", "#")

            last_err = None
            for guid in guids:
                self._device_guid = guid
                for prefix in ["\\\\.\\", "\\\\?\\"]:
                    self._filename = prefix + self._device_name + "#" + self._device_guid
                    try:
                        self._dev = win32file.CreateFile(
                            self._filename, GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
                        )
                        if self._dev != -1:
                            print(f"[BLE Patch] Connected to adapter using GUID {guid} and prefix {prefix}")
                            return
                    except Exception as e:
                        last_err = e

            raise last_err or Exception("Failed to open connection to the bluetooth adapter using any GUID/prefix")

        bless_adapter.BLEAdapter.__init__ = patched_init
        print("[BLE Patch] Applied Windows BLEAdapter monkeypatch successfully.")

        # Monkeypatch GattServiceProvider to handle the start_advertising parameter count issue on Windows
        try:
            import winrt.windows.devices.bluetooth.genericattributeprofile as gap
            
            orig_start_advertising = gap.GattServiceProvider.start_advertising
            has_with_params = hasattr(gap.GattServiceProvider, "start_advertising_with_parameters")

            def patched_start_advertising(self, *args, **kwargs):
                if args or kwargs:
                    if has_with_params:
                        return self.start_advertising_with_parameters(*args, **kwargs)
                    try:
                        return orig_start_advertising(self, *args, **kwargs)
                    except TypeError as te:
                        if "parameter" in str(te).lower():
                            print("[BLE Patch] start_advertising failed with parameters. Retrying with no parameters...")
                            return orig_start_advertising(self)
                        raise te
                else:
                    return orig_start_advertising(self)

            gap.GattServiceProvider.start_advertising = patched_start_advertising
            print("[BLE Patch] Applied Windows GattServiceProvider start_advertising direct monkeypatch successfully.")
        except Exception as e:
            print(f"[BLE Patch] Failed to apply Windows GattServiceProvider monkeypatch: {e}")

        # Monkeypatch BlessServerWinRT.start to handle cached/already-advertising state and prevent infinite blocking on wait()
        try:
            import bless.backends.winrt.server as bless_winrt_server
            orig_winrt_start = bless_winrt_server.BlessServerWinRT.start

            async def patched_winrt_start(self, *args, **kwargs):
                already_advertising = False
                for uuid, service in self.services.items():
                    if service.service_provider is not None and service.service_provider.advertisement_status == 2:
                        already_advertising = True
                        break
                if already_advertising:
                    print("[BLE Patch] Already advertising, setting advertising event to prevent blocking.")
                    self._advertising_started.set()
                
                orig_wait = self._advertising_started.wait
                self._advertising_started.wait = lambda timeout=2.0: orig_wait(timeout=timeout)
                try:
                    await orig_winrt_start(self, *args, **kwargs)
                finally:
                    self._advertising_started.wait = orig_wait

            bless_winrt_server.BlessServerWinRT.start = patched_winrt_start
            print("[BLE Patch] Applied Windows BlessServerWinRT start monkeypatch successfully.")
        except Exception as e:
            print(f"[BLE Patch] Failed to apply Windows BlessServerWinRT start monkeypatch: {e}")

    except Exception as e:
        print(f"[BLE Patch] Failed to apply Windows BLEAdapter monkeypatch: {e}")

from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions,
)

# BlessAdvertisementData was added in bless master (post-0.3.0, PR #159).
# It allows passing a single advertising payload instead of letting each
# GATT service advertise independently (which caused macOS clients to see
# 3 phantom devices when Windows hosts).
#
# Try to import it; if running on older bless (0.3.0 from PyPI), fall back
# to the legacy per-service advertising path.
try:
    from bless.backends.advertisement import BlessAdvertisementData
    _HAS_BLESS_ADVERTISEMENT_DATA = True
except ImportError:
    BlessAdvertisementData = None  # type: ignore
    _HAS_BLESS_ADVERTISEMENT_DATA = False

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

# CPS (Cycling Power Service)
CPS_SERVICE_UUID = "00001818-0000-1000-8000-00805f9b34fb"
CPS_MEASUREMENT_UUID = "00002a63-0000-1000-8000-00805f9b34fb"
CPS_FEATURE_UUID = "00002a65-0000-1000-8000-00805f9b34fb"

# Current platform
_PLATFORM = platform.system()  # "Darwin", "Windows", or "Linux"


class BLECadenceServer:
    """BLE server: CSC + FTMS, via bless (cross-platform)."""

    def __init__(self):
        self._server: Optional[BlessServer] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

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

    @staticmethod
    def calculate_power(rpm: float,
                       model: Optional[str] = None,
                       ratio: Optional[float] = None,
                       circumference_m: Optional[float] = None) -> int:
        """Compute estimated power (W) from crank RPM using the configured trainer model.

        This is a @staticmethod so it can be unit-tested without instantiating
        the BLE server (which would launch the asyncio loop and advertising).

        Args:
            rpm: Cadence in revolutions per minute.
            model: Override config.POWER_MODEL ("linear" | "fluid" | "mag").
                   If None, uses config.POWER_MODEL at call time.
            ratio: Override config.WHEEL_TO_CRANK_RATIO (chainring/cog).
            circumference_m: Override config.WHEEL_CIRCUMFERENCE_M.

        Returns:
            Power in Watts, clamped to [0, 0x7FFF] (sint16 positive range).

        Models:
            linear — P = rpm * 0.8 + 30   (debug only, not accurate to any real trainer)
            fluid  — Kurt Kinetic Road Machine official curve:
                     P = 5.244820 * S + 0.019168 * S^3   (S = wheel speed in mph)
                     Source: https://kurtkinetic.com/
            mag    — Generic magnetic trainer estimate (±20-30% uncertainty):
                     P = 0.1 * S^2 + 3.0 * S + 10   (S = wheel speed in km/h)
        """
        if rpm < 1.0:
            return 0

        model = (model or getattr(config, "POWER_MODEL", "linear")).lower()
        ratio = ratio if ratio is not None else getattr(config, "WHEEL_TO_CRANK_RATIO", 2.0)
        circ = circumference_m if circumference_m is not None else getattr(config, "WHEEL_CIRCUMFERENCE_M", 2.105)

        if model == "linear":
            power = rpm * 0.8 + 30.0
        else:
            # Convert crank RPM to wheel speed in km/h
            speed_kmh = (rpm * ratio * circ * 60.0) / 1000.0

            if model == "fluid":
                # Kurt Kinetic Road Machine — official coefficients
                # (0.019168 is the current official value; older 0.01968 was 2.7% stiffer)
                speed_mph = speed_kmh / 1.609344
                power = 5.244820 * speed_mph + 0.019168 * (speed_mph ** 3)
            elif model == "mag":
                power = 0.1 * (speed_kmh ** 2) + 3.0 * speed_kmh + 10.0
            else:
                # Unknown model → fallback to linear
                power = rpm * 0.8 + 30.0

        return int(max(0, min(0x7FFF, power)))

    def _calculate_power(self, rpm: float) -> int:
        """Instance wrapper around the static calculate_power for internal use."""
        return self.calculate_power(rpm)

    # ========================================================================
    # Packet builders
    # ========================================================================
    def _build_csc_measurement(self) -> bytearray:
        """CSC Measurement: flags + wheel + crank.
        
        Spec: CSC Measurement characteristic (0x2A5B).
        Flags: Bit 0 (Wheel Revolution Data Present) = 1
               Bit 1 (Crank Revolution Data Present) = 1
        Format: flags (uint8), cumulative_wheel_revs (uint32), last_wheel_event_time (uint16),
                cumulative_crank_revs (uint16), last_crank_event_time (uint16).
        Units: last event times are in 1/1024 second units.
        """
        with self._lock:
            flags = 0x03  # Wheel + Crank present
            wheel_revs = int(self._cumulative_revolutions * config.WHEEL_TO_CRANK_RATIO) & 0xFFFFFFFF
            wheel_time = self._last_event_time_1024 & 0xFFFF
            crank_revs = self._cumulative_revolutions & 0xFFFF
            crank_time = self._last_event_time_1024 & 0xFFFF
        return bytearray(struct.pack("<BIHHH", flags, wheel_revs, wheel_time, crank_revs, crank_time))

    def _build_indoor_bike_data(self) -> bytearray:
        """FTMS Indoor Bike Data: flags + speed + cadence + power.
        
        Spec: FTMS Indoor Bike Data characteristic (0x2AD2).
        Flags (uint16): 
          - Bit 0: More Data = 0 (implies instantaneous speed is present, uint16, 0.01 km/h)
          - Bit 2: Average Speed present = 0
          - Bit 2 (of flags value): Instantaneous Cadence present = 1 (uint16, 0.5 RPM)
          - Bit 6: Instantaneous Power present = 1 (sint16, 1 Watt)
        Layout: flags (16-bit), speed (16-bit), cadence (16-bit), power (16-bit signed).
        """
        with self._lock:
            flags = (1 << 2) | (1 << 6)  # cadence + power present
            # Speed in 0.01 km/h:
            # speed_kmh = (RPM * ratio * circumference * 60) / 1000.0
            # speed_raw = speed_kmh * 100 = (RPM * ratio * circumference * 60) / 10.0
            speed_raw_val = (self._current_rpm * config.WHEEL_TO_CRANK_RATIO
                             * config.WHEEL_CIRCUMFERENCE_M * 60.0) / 10.0
            speed_raw = int(max(0, min(0xFFFF, speed_raw_val)))
            # Cadence in 0.5 rpm units
            cadence_raw = int(max(0, min(0xFFFF, self._current_rpm * 2.0)))
            # Power in Watts (sint16)
            power_w = self._calculate_power(self._current_rpm)
        return bytearray(struct.pack("<HHHh", flags, speed_raw, cadence_raw, power_w))

    def _build_cps_feature(self) -> bytearray:
        # Cycling Power Feature:
        # Bit 3: Crank Revolution Data Supported (value 0x00000008)
        feature = 0x00000008
        return bytearray(struct.pack("<I", feature))

    def _build_cps_measurement(self) -> bytearray:
        """Cycling Power Measurement: flags + instantaneous power + crank data.
        
        Spec: CPS Measurement characteristic (0x2A63).
        Flags (uint16): Bit 5 (Crank Revolution Data Present) = 1
        Layout: flags (uint16), instantaneous_power (sint16), cumulative_crank_revs (uint16),
                last_crank_event_time (uint16).
        Units: last event times are in 1/1024 second units.
        """
        with self._lock:
            flags = 0x0020  # Crank Revolution Data Present (Bit 5)
            power_w = self._calculate_power(self._current_rpm)
            crank_revs = self._cumulative_revolutions & 0xFFFF
            crank_time = self._last_event_time_1024 & 0xFFFF
        return bytearray(struct.pack("<HhHH", flags, power_w, crank_revs, crank_time))

    def _build_csc_feature(self) -> bytearray:
        return bytearray(struct.pack("<H", 0x0003))  # Wheel + Crank supported

    def _build_ftms_feature(self) -> bytearray:
        # FTMS Feature bitmask (32-bit features + 32-bit target settings = 8 bytes)
        # Bit 1: Cadence Supported
        # Bit 14: Power Measurement Supported
        features = (1 << 1) | (1 << 14)
        target_setting_features = 0
        return bytearray(struct.pack("<II", features, target_setting_features))

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
        cps_feature_value = self._build_cps_feature()

        gatt: Dict = {
            # ============== CPS Service ==============
            CPS_SERVICE_UUID: {
                # CPS Measurement: Notify + Read -> Value must be None
                CPS_MEASUREMENT_UUID: {
                    "Properties": (
                        GATTCharacteristicProperties.notify
                        | GATTCharacteristicProperties.read
                    ),
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": None,
                },
                # CPS Feature: Read-only -> static Value
                CPS_FEATURE_UUID: {
                    "Properties": GATTCharacteristicProperties.read,
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": cps_feature_value,
                },
            },
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
            print("[BLE] Building GATT services (CSC + CPS + FTMS)...")
            gatt_dict = self._build_gatt_dict()
            await self._server.add_gatt(gatt_dict)

            print("[BLE] Starting advertising...")
            # Build a BlessAdvertisementData payload so we have a SINGLE
            # advertisement containing the device name + all service UUIDs,
            # instead of letting each GATT service advertise independently.
            #
            # Background: bless 0.3.0 (and earlier) called
            #   service_provider.start_advertising() once PER GATT service,
            #   which on Windows WinRT caused macOS clients to see N phantom
            #   devices (one per service) instead of 1 unified device.
            #
            # The new BlessAdvertisementData API (PR #159, merged 2025)
            # lets us pass a single advertising payload.
            #
            # Note on platform behavior (per BlessAdvertisementData.__post_init__):
            #   - macOS: local_name + service_uuids are NOT used by the new API
            #     (CoreBluetooth uses BlessServer.name + prioritize_local_name kwarg)
            #   - Windows: local_name is used via _adapter.set_local_name()
            #   - Linux: all fields are used
            #
            # So we still pass prioritize_local_name=True on macOS for the name,
            # AND we pass advertisement_data for the Windows/Linux paths.
            adv_data = None
            if _HAS_BLESS_ADVERTISEMENT_DATA:
                # The 3 service UUIDs we advertise (so clients can discover us
                # as a smart trainer via FTMS, plus as a power meter via CPS,
                # plus as a cadence sensor via CSC).
                service_uuids_16bit = [
                    "00001826-0000-1000-8000-00805f9b34fb",  # FTMS (0x1826)
                    "00001818-0000-1000-8000-00805f9b34fb",  # CPS  (0x1818)
                    "00001816-0000-1000-8000-00805f9b34fb",  # CSC  (0x1816)
                ]
                adv_data = BlessAdvertisementData(
                    local_name=config.BLE_DEVICE_NAME,
                    service_uuids=service_uuids_16bit,
                    is_connectable=True,
                    is_discoverable=True,
                )

            if _PLATFORM == "Darwin":
                # macOS: prioritize_local_name=True puts the device name in
                # the primary advertisement (required for MyWhoosh to show
                # "Velo" instead of "device-XXXX" before Scan Response arrives).
                # advertisement_data is also passed but on macOS the local_name
                # and service_uuids fields are ignored (the kwarg controls it).
                await self._server.start(
                    advertisement_data=adv_data,
                    prioritize_local_name=True,
                )
            else:
                # Windows + Linux: advertisement_data is the primary control.
                # On Windows, local_name propagates via _adapter.set_local_name().
                # On Linux, all fields are used by the BlueZ backend.
                await self._server.start(advertisement_data=adv_data)
            self._status = "Advertising"
            print(f"[BLE] '{config.BLE_DEVICE_NAME}' is advertising.")
            print(f"[BLE] Open MyWhoosh -> Device Connection -> Controllable -> pair with '{config.BLE_DEVICE_NAME}'.")

            # Notification loop
            counter = 0
            while self._running:
                await asyncio.sleep(config.BLE_NOTIFY_INTERVAL_SEC)
                if not self._running:
                    break
                await self._send_ftms_notification()
                await self._send_cps_notification()
                await self._send_csc_notification()
                counter += 1
                if counter % 5 == 0:
                    try:
                        connected = await self._server.is_connected()
                        self._client_connected = bool(connected)
                    except Exception:
                        pass
                    with self._lock:
                        revs = self._cumulative_revolutions
                        rpm = self._current_rpm
                    print(f"[BLE] Connected: {self._client_connected} | "
                          f"Revs: {revs} | "
                          f"RPM: {rpm:.1f}")

        except Exception as e:
            self._status = f"Error: {e}"
            print(f"[BLE] ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self._server is not None:
                try:
                    print("[BLE] Stopping BlessServer...")
                    await self._server.stop()
                    print("[BLE] BlessServer stopped gracefully.")
                except Exception as stop_err:
                    print(f"[BLE] Error stopping BlessServer: {stop_err}")

    async def _send_csc_notification(self):
        """Send CSC notification immediately when a revolution occurs."""
        try:
            if self._server is not None:
                csc_value = self._build_csc_measurement()
                self._server.get_characteristic(CSC_MEASUREMENT_UUID).value = csc_value
                self._server.update_value(CSC_SERVICE_UUID, CSC_MEASUREMENT_UUID)
        except Exception as e:
            print(f"[BLE] CSC Notification error: {e}")

    async def _send_ftms_notification(self):
        """Send FTMS notification (heartbeat)."""
        try:
            if self._server is not None:
                bike_value = self._build_indoor_bike_data()
                self._server.get_characteristic(FTMS_INDOOR_BIKE_DATA_UUID).value = bike_value
                self._server.update_value(FTMS_SERVICE_UUID, FTMS_INDOOR_BIKE_DATA_UUID)
        except Exception as e:
            print(f"[BLE] FTMS Notification error: {e}")

    async def _send_cps_notification(self):
        """Send CPS notification."""
        try:
            if self._server is not None:
                cps_value = self._build_cps_measurement()
                self._server.get_characteristic(CPS_MEASUREMENT_UUID).value = cps_value
                self._server.update_value(CPS_SERVICE_UUID, CPS_MEASUREMENT_UUID)
        except Exception as e:
            print(f"[BLE] CPS Notification error: {e}")

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
        should_notify = False
        with self._lock:
            self._current_rpm = current_rpm
            if total_revolutions != self._cumulative_revolutions:
                self._cumulative_revolutions = total_revolutions
                
                # Use the actual timestamp when the revolution occurred to prevent
                # timing drift and out-of-sync packets that confuse MyWhoosh.
                event_time = last_event_time if last_event_time is not None else time.time()
                elapsed = event_time - self._base_time
                self._last_event_time_1024 = int(elapsed * 1024) & 0xFFFF
                should_notify = True
                
        # Schedule immediate BLE notification to ensure the packet arrives
        # at MyWhoosh with precise event-driven arrival spacing.
        if should_notify and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._send_csc_notification(), self._loop)
            except Exception:
                pass

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
            try:
                self._thread.join(timeout=3.0)
            except KeyboardInterrupt:
                pass
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
