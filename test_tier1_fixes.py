"""VeloTracker — Unit tests for Tier 1 fixes (pytest).

Run with: pytest test_tier1_fixes.py -v

These tests validate the 10 quick-win fixes from the production-grade audit
(docs/audit/VELTRACKER_AUDIT.md). They are pure-logic tests that don't require
a camera or BLE hardware, so they can run in CI on any platform.
"""

import sys
import os
import math
import time
import struct
from pathlib import Path

# Make the project importable from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest
import numpy as np

import config
from modules.ble_server import BLECadenceServer
from modules.rpm_calculator import RPMCalculator, StickerKalmanFilter


# ============================================================================
# Helpers
# ============================================================================

def simulate_pedaling(rpm_target: float, frames: int = 300, fps: float = 30.0):
    """Drive an RPMCalculator with a synthetic circular pedal motion at a target RPM.

    Returns the calculator after simulation.
    """
    calc = RPMCalculator()
    center = (320, 240)
    radius = 100.0
    omega = (rpm_target / 60.0) * 2 * math.pi  # rad/sec
    t0 = time.time()
    for i in range(frames):
        t = t0 + i * (1.0 / fps)
        angle = omega * (t - t0)
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle)
        calc.update(int(x), int(y), t)
    return calc


# ============================================================================
# BUG #2 — WHEEL_TO_CRANK_RATIO consistency
# ============================================================================

class TestDrivetrainConfig:
    """Validate that config exposes CHAINRING and COG, and the ratio is consistent."""

    def test_chainring_and_cog_exist(self):
        assert hasattr(config, "CHAINRING"), "config.CHAINRING should be defined"
        assert hasattr(config, "COG"), "config.COG should be defined"

    def test_ratio_matches_chainring_over_cog(self):
        expected = config.CHAINRING / config.COG
        assert config.WHEEL_TO_CRANK_RATIO == pytest.approx(expected, rel=1e-6), (
            f"WHEEL_TO_CRANK_RATIO={config.WHEEL_TO_CRANK_RATIO} should equal "
            f"CHAINRING/COG={expected}"
        )

    def test_default_50x17_for_ftp_test(self):
        """50x17 is the TrainerRoad/Kinetic FTP-test standard gear."""
        # Default config should match this — change the assertion if you intentionally
        # ship a different default ratio.
        assert config.CHAINRING == 50
        assert config.COG == 17
        assert config.WHEEL_TO_CRANK_RATIO == pytest.approx(50 / 17, rel=1e-6)


# ============================================================================
# BUG #9 — BLE device name
# ============================================================================

class TestBLEDeviceName:
    """The device name should be short enough to fit the macOS advertising
    payload budget (was 'VeloTracker' — too long for some stacks).
    Now using 'V' (1 char) for maximum compatibility across all BLE stacks."""

    def test_device_name_is_short_and_identifiable(self):
        # 'V' is 1 char — fits all BLE stacks (macOS CoreBluetooth,
        # Windows WinRT, Linux BlueZ) without truncation, and leaves
        # maximum room for service UUIDs in the 28-byte advertisement.
        # The name is intentionally minimal because:
        #   - On macOS, the user identifies the device by 'V' in MyWhoosh
        #   - On Windows, the device name falls back to system adapter name
        #     anyway (WinRT limitation — see README 'Known Issues')
        #   - On Linux, the name is used as-is
        assert config.BLE_DEVICE_NAME == "V", (
            f"Expected 'V' (1 char, max compatibility), got '{config.BLE_DEVICE_NAME}'"
        )

    def test_device_name_fits_all_advert_budgets(self):
        """All BLE stacks (macOS/Windows/Linux) limit adv payload to ~28 usable bytes.
        Budget: Flags (3B) + 3 × 16-bit SVCS (8B) + Local Name (len+2) <= 28.
        """
        name = config.BLE_DEVICE_NAME
        # 3 (Flags) + 2 (AD type+len) + 6 (3 SVCS × 2 bytes) + 2 (Local Name header) + len(name)
        adv_size = 3 + 2 + 6 + 2 + len(name)
        assert adv_size <= 28, (
            f"Advertising payload would be {adv_size} bytes, exceeds 28-byte budget"
        )


# ============================================================================
# BUG #14 — Glitch rejection threshold tightened
# ============================================================================

class TestGlitchRejection:
    """Glitch threshold should be 0.3 rad (was 1.0 — never triggered for humans)."""

    def test_max_delta_angle_is_tight(self):
        assert RPMCalculator.MAX_DELTA_ANGLE == 0.3, (
            f"Expected 0.3, got {RPMCalculator.MAX_DELTA_ANGLE}"
        )

    def test_normal_pedaling_not_rejected(self):
        """At 90 RPM, 30 FPS, per-frame angle delta is ~18° (~0.31 rad).
        With the adaptive threshold (1.5 * omega * dt + 0.1), this should NOT be rejected.
        """
        calc = simulate_pedaling(rpm_target=90, frames=300, fps=30.0)
        # After 300 frames, we should be in TRACKING phase with RPM close to 90
        assert calc.phase == "TRACKING"
        assert 80 <= calc.rpm <= 100, f"Expected ~90 RPM, got {calc.rpm}"

    def test_spike_is_rejected(self):
        """A sudden huge angle jump should be rejected (not corrupt total_angle)."""
        calc = RPMCalculator()
        center = (320, 240)
        radius = 100.0
        omega = (90 / 60.0) * 2 * math.pi
        t0 = time.time()

        # Warm up with 120 frames — 90 for calibration, 30 for tracking to establish prev_angle
        for i in range(120):
            t = t0 + i / 30.0
            angle = omega * (t - t0)
            x = center[0] + radius * math.cos(angle)
            y = center[1] + radius * math.sin(angle)
            calc.update(int(x), int(y), t)

        # Verify we're in TRACKING and prev_angle is set
        assert calc.phase == "TRACKING"
        assert calc._prev_angle is not None, "prev_angle should be set after warm-up"

        # Now inject a sudden spike — a 5-radian jump (impossible at human cadence)
        prev_angle = calc._prev_angle
        spike_angle = prev_angle + 5.0
        # Position at the spike angle
        sx = center[0] + radius * math.cos(spike_angle)
        sy = center[1] + radius * math.sin(spike_angle)
        t_spike = t0 + 121 / 30.0
        calc.update(int(sx), int(sy), t_spike)

        # The spike should NOT have been added to total_angle
        # If it had been accepted, total_angle would jump by ~5 rad
        # With our threshold, the spike is rejected, so total_angle grows by ~0.3 rad (normal frame)
        # We just check that the tracker didn't go crazy
        assert abs(calc._omega) < 50, f"Omega should not explode after spike, got {calc._omega}"


# ============================================================================
# BUG #15 — Redundant EMA removed from detector
# ============================================================================

class TestDetectorNoRedundantEMA:
    """The detector should NOT have a redundant EMA smoother (Kalman in RPMCalc is enough)."""

    def test_smooth_cx_not_used_in_output(self):
        """DetectionResult.cx should equal raw_cx (no EMA smoothing applied)."""
        # We can't easily run a full detection without a real frame + camera,
        # but we can verify the detector.py source doesn't reference _smooth_cx in the return.
        detector_src = Path("modules/detector.py").read_text(encoding="utf-8")
        # The return statement should use raw_cx, not _smooth_cx
        assert "cx=raw_cx" in detector_src or "cx=raw_cx," in detector_src, (
            "Detector should return raw_cx directly (no EMA). "
            "Check the DetectionResult construction in detector.py"
        )


# ============================================================================
# BUG #19 — ble_diagnostic uses _calculate_power (not hardcoded linear)
# ============================================================================

class TestBLEDiagnosticUsesRealPower:
    """ble_diagnostic.py should call server._calculate_power, not hardcode linear formula."""

    def test_no_hardcoded_linear_formula(self):
        diag_src = Path("ble_diagnostic.py").read_text(encoding="utf-8")
        # The old buggy line was: expected_power = int(current_rpm * 0.8 + 30.0)
        assert "current_rpm * 0.8 + 30" not in diag_src, (
            "ble_diagnostic.py still has hardcoded linear power formula"
        )
        assert "_calculate_power(current_rpm)" in diag_src, (
            "ble_diagnostic.py should use server._calculate_power(current_rpm)"
        )


# ============================================================================
# BUG #20 — Unified decay factor
# ============================================================================

class TestUnifiedDecay:
    """Both decay paths should use config.RPM_DECAY_FACTOR (was 0.80 vs 0.85)."""

    def test_no_hardcoded_080_decay(self):
        """The _estimate_rpm method should reference RPM_DECAY_FACTOR, not hardcode 0.80."""
        rpm_src = Path("modules/rpm_calculator.py").read_text(encoding="utf-8")
        # The old buggy line was: self._current_rpm *= 0.80
        # We allow the literal 0.80 to appear in comments, but not in a *= assignment
        bad_pattern = "self._current_rpm *= 0.80"
        assert bad_pattern not in rpm_src, (
            f"Found '{bad_pattern}' in rpm_calculator.py — should use config.RPM_DECAY_FACTOR"
        )

    def test_estimate_rpm_uses_config_decay(self):
        """_estimate_rpm should reference RPM_DECAY_FACTOR for the decay path."""
        rpm_src = Path("modules/rpm_calculator.py").read_text(encoding="utf-8")
        # Find the _estimate_rpm method (between def _estimate_rpm and the next def)
        start = rpm_src.find("def _estimate_rpm")
        end = rpm_src.find("def ", start + 10)
        estimate_rpm_body = rpm_src[start:end]
        assert "RPM_DECAY_FACTOR" in estimate_rpm_body, (
            "_estimate_rpm should reference config.RPM_DECAY_FACTOR for the decay path"
        )


# ============================================================================
# BUG #25 — _calculate_power is a @staticmethod
# ============================================================================

class TestCalculatePowerIsStatic:
    """calculate_power should be callable without instantiating the BLE server."""

    def test_calculate_power_is_staticmethod(self):
        """Direct check: can we call BLECadenceServer.calculate_power(rpm) without instance?"""
        # This should NOT raise — if calculate_power is a @staticmethod, it works on the class
        watts = BLECadenceServer.calculate_power(90.0, model="linear")
        assert watts == 102, f"Expected 102 W for 90 RPM linear, got {watts}"

    def test_fluid_model_matches_kurt_kinetic_spec(self):
        """Validate against Kurt Kinetic's official curve anchors:
        - 16.1 mph ≈ 164 W
        - 20 mph ≈ 258 W
        - 25 mph ≈ 431 W

        With 50x17 gear and 700x25c tire:
        - 16.1 mph = 25.9 km/h = 69.7 RPM (50x17, 2.105m circ)
        - 20 mph = 32.2 km/h = 86.7 RPM
        - 25 mph = 40.2 km/h = 108.4 RPM
        """
        ratio = 50 / 17  # default
        circ = 2.105
        # mph to km/h to RPM: RPM = mph_kmh / (ratio * circ * 0.06)
        for mph_target, expected_watts in [(16.1, 164), (20.0, 258), (25.0, 431)]:
            kmh = mph_target * 1.609344
            rpm = kmh / (ratio * circ * 0.06)
            watts = BLECadenceServer.calculate_power(rpm, model="fluid",
                                                     ratio=ratio, circumference_m=circ)
            # ±5% tolerance (the official curve itself varies by ±2-3% with temperature)
            assert abs(watts - expected_watts) / expected_watts < 0.05, (
                f"At {mph_target} mph (RPM={rpm:.1f}): expected ~{expected_watts} W, got {watts} W"
            )

    def test_zero_rpm_returns_zero(self):
        for model in ["linear", "fluid", "mag"]:
            assert BLECadenceServer.calculate_power(0.0, model=model) == 0
            assert BLECadenceServer.calculate_power(0.5, model=model) == 0  # below 1.0 RPM threshold

    def test_negative_rpm_returns_zero(self):
        """Negative RPM (e.g. backpedaling) should not produce negative power."""
        for model in ["linear", "fluid", "mag"]:
            assert BLECadenceServer.calculate_power(-50.0, model=model) == 0

    def test_high_rpm_clamped_to_sint16_max(self):
        """At extreme RPM, power should be clamped to 0x7FFF (32767 W) — sint16 positive max."""
        # 1000 RPM in fluid model would give huge watts
        watts = BLECadenceServer.calculate_power(1000.0, model="fluid")
        assert watts <= 0x7FFF, f"Power should be clamped to 0x7FFF, got {watts}"

    def test_unknown_model_falls_back_to_linear(self):
        """An unknown model name should fall back to the linear formula, not crash."""
        watts = BLECadenceServer.calculate_power(90.0, model="nonexistent_model")
        assert watts == 102  # same as linear

    def test_instance_method_still_works(self):
        """The instance method _calculate_power should still work (backward compat)."""
        # We can't instantiate BLECadenceServer without launching BLE, but we can
        # verify the method exists and delegates to the staticmethod.
        # This is a structural test.
        assert hasattr(BLECadenceServer, "_calculate_power")
        assert hasattr(BLECadenceServer, "calculate_power")


# ============================================================================
# BUG #26 — bless version pinned in requirements.txt
# ============================================================================

class TestRequirementsPinned:
    """requirements.txt should pin bless to a stable PyPI version range."""

    def test_bless_version_pinned(self):
        req_text = Path("requirements.txt").read_text(encoding="utf-8")
        # Should contain a bless line with both lower and upper bounds
        # e.g. "bless>=0.3.0,<0.4.0" (PyPI stable pin)
        bless_lines = [l for l in req_text.splitlines() if l.strip().startswith("bless")]
        assert len(bless_lines) >= 1, "bless should be in requirements.txt"
        bless_line = bless_lines[0]
        # Must have upper bound (<) — prevents unexpected breakage from minor upgrades
        # Must NOT be a git pin (we reverted to PyPI stable)
        assert ("<" in bless_line) and ("git+" not in bless_line), (
            f"bless should be PyPI-pinned with upper bound. Got: {bless_line}"
        )

    def test_requirements_txt_is_ascii_only(self):
        """requirements.txt MUST be 100% ASCII because pip on Windows defaults
        to cp1252 encoding, which can't decode UTF-8 chars like em-dashes (—),
        arrows (→), or emojis (⚠️). This caused a UnicodeDecodeError on Windows
        in a previous commit (we used — and ⚠️ in comments).

        See: https://github.com/MahmoudDabachii2004/velotracker/issues
        """
        with open("requirements.txt", "rb") as f:
            content = f.read()
        non_ascii = [(i, b) for i, b in enumerate(content) if b > 127]
        assert not non_ascii, (
            f"requirements.txt must be 100% ASCII (pip on Windows uses cp1252). "
            f"Found {len(non_ascii)} non-ASCII bytes at positions: "
            f"{[i for i, _ in non_ascii[:5]]}. "
            f"Replace em-dashes (—) with hyphens (-), emojis with [WARNING], "
            f"arrows (→) with ->, etc."
        )

    def test_no_bleak_upper_pin_on_windows(self):
        """We previously tried 'bleak<1.0' on Windows, but bless 0.3.0 REQUIRES
        bleak>=1.1.1, so bleak<1.0 is impossible. The conflict is inside bless itself.
        requirements.txt should NOT pin bleak to <1.0 anywhere."""
        req_text = Path("requirements.txt").read_text(encoding="utf-8")
        # The old broken line was: bleak>=0.22,<1.0; sys_platform == "win32"
        assert "bleak>=0.22,<1.0" not in req_text, (
            "bleak<1.0 pin was removed — bless 0.3.0 itself requires bleak>=1.1.1"
        )

    def test_bless_pypi_pin(self):
        """bless should be pinned to a stable PyPI version range (0.3.x).
        We tried git master (commit a27e1c25) but it introduced breaking API
        changes that we couldn't work around cleanly. Reverted to PyPI stable."""
        req_text = Path("requirements.txt").read_text(encoding="utf-8")
        bless_lines = [l for l in req_text.splitlines() if l.strip().startswith("bless")]
        assert len(bless_lines) >= 1, "bless should be in requirements.txt"
        bless_line = bless_lines[0].strip()
        # Must NOT be a git pin (we reverted to PyPI stable)
        assert "git+" not in bless_line, (
            f"bless should be PyPI-pinned (not git). Got: {bless_line}"
        )
        # Must have upper bound to prevent unexpected upgrades
        assert "<" in bless_line, (
            f"bless should have upper bound pin (e.g. <0.4.0). Got: {bless_line}"
        )


# ============================================================================
# Platform compatibility sanity check
# ============================================================================

class TestPlatformCompatibility:
    """Verify the project's documented platform constraints are consistent."""

    def test_windows_python_311_required(self):
        """On Windows, only Python 3.11 is supported (due to bless 0.3.0
        dependency conflict on Python 3.12+ — see requirements.txt).
        If we're on Windows + Python 3.12+, fail loudly so the user knows
        to switch."""
        import platform
        import sys

        if platform.system() == "Windows":
            py_version = sys.version_info
            if py_version >= (3, 12):
                pytest.fail(
                    f"VeloTracker does NOT support Python {py_version.major}.{py_version.minor} "
                    f"on Windows. bless 0.3.0 has an internal dependency conflict "
                    f"on Python 3.12+. Please install Python 3.11.9 from "
                    f"https://www.python.org/downloads/release/python-3119/ and recreate "
                    f"your venv with: py -3.11 -m venv .venv"
                )
            # On Windows + Python 3.11, we're good
            assert py_version >= (3, 9), "Python 3.9+ required"

    def test_bless_0_3_0_api_compatible(self):
        """We use bless 0.3.0 from PyPI (stable). Verify that the API we depend
        on is available:
          - GATTAttributePermissions.writeable (NOT 'writable' — that's bless master)
          - BlessAdvertisementData should NOT exist (bless master API)
        """
        from bless import GATTAttributePermissions
        # bless 0.3.0 uses 'writeable' (with the 'e')
        assert hasattr(GATTAttributePermissions, "writeable"), (
            "GATTAttributePermissions.writeable should exist in bless 0.3.0. "
            "If you see this fail, you may have bless master installed — "
            "reinstall with: pip install 'bless>=0.3.0,<0.4.0'"
        )
        # BlessAdvertisementData should NOT be importable (bless 0.3.0 doesn't have it)
        try:
            from bless.backends.advertisement import BlessAdvertisementData  # noqa: F401
            pytest.fail(
                "BlessAdvertisementData should NOT be available — we use bless 0.3.0 "
                "from PyPI which doesn't have it. If you have bless master installed, "
                "reinstall with: pip install 'bless>=0.3.0,<0.4.0'"
            )
        except ImportError:
            # Expected — bless 0.3.0 doesn't have BlessAdvertisementData
            pass


# ============================================================================
# BUG #18 — test_filters.py uses cross-platform path
# ============================================================================

class TestTestFiltersCrossPlatform:
    """test_filters.py should NOT hardcode a Windows path."""

    def test_no_hardcoded_windows_path(self):
        tf_src = Path("test_filters.py").read_text(encoding="utf-8")
        # The old buggy line was: sys.path.insert(0, r"c:\Users\newMahmoud\velotracker")
        assert "c:\\Users" not in tf_src and "C:\\Users" not in tf_src, (
            "test_filters.py should not hardcode Windows user path"
        )
        # Should use the cross-platform pattern
        assert "os.path.dirname(os.path.abspath(__file__))" in tf_src, (
            "test_filters.py should use os.path.dirname(os.path.abspath(__file__)) for sys.path"
        )


# ============================================================================
# BUG #13 — README mentions Taubin (not Kasa)
# ============================================================================

class TestReadmeMentionsTaubin:
    """README should say 'Taubin' (the actual algorithm), not 'Kasa'."""

    def test_no_kasa_in_readme(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        # Kasa is allowed in the comparison sentence ("Taubin is much more stable than Kasa")
        # but NOT in standalone claims like "Kasa circle fit + OLS regression -> RPM"
        # We check that "Kasa circle fit" doesn't appear (it should be "Taubin circle fit")
        assert "Kasa circle fit" not in readme, (
            "README should say 'Taubin circle fit', not 'Kasa circle fit'"
        )

    def test_taubin_mentioned_in_readme(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        assert "Taubin" in readme, "README should mention Taubin (the actual algorithm used)"


# ============================================================================
# Documentation: Known Issues section (cross-platform BLE quirks)
# ============================================================================

class TestKnownIssuesDocumented:
    """README must document the cross-platform BLE device name quirks
    discovered during testing (macOS suffix, Windows Device-XXXX, 2 devices)."""

    def test_known_issues_section_exists(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        assert "## Known Issues" in readme, (
            "README should have a 'Known Issues' section documenting BLE quirks"
        )

    def test_macos_suffix_documented(self):
        """'Velo-XXXX' suffix on macOS client when Mac hosts must be documented."""
        readme = Path("README.md").read_text(encoding="utf-8")
        assert "Velo-" in readme or "suffix" in readme.lower(), (
            "README should document the 'Velo-XXXX' suffix issue on macOS clients"
        )

    def test_windows_device_xxxx_documented(self):
        """'Device-XXXXXX' on macOS client when Windows hosts must be documented."""
        readme = Path("README.md").read_text(encoding="utf-8")
        assert "Device-" in readme, (
            "README should document the 'Device-XXXXXX' fallback name issue "
            "when Windows hosts and macOS client connects"
        )

    def test_multiple_devices_windows_documented(self):
        """The '2 devices visible' issue when Windows hosts must be documented."""
        readme = Path("README.md").read_text(encoding="utf-8")
        # Either "2 devices" or "two devices" or "multiple devices"
        assert any(p in readme.lower() for p in ["2 devices", "two devices", "multiple devices"]), (
            "README should document that Windows host can show 2 phantom devices on macOS client"
        )


# ============================================================================
# Integration: simulate_pedaling still works end-to-end
# ============================================================================

class TestEndToEndSimulation:
    """Sanity check: with all the fixes applied, the RPMCalculator still works correctly."""

    @pytest.mark.parametrize("target_rpm,expected_tolerance", [
        (60, 5),    # 60 RPM ±5
        (80, 5),
        (90, 5),
        (100, 5),
        (120, 5),
    ])
    def test_rpm_detection_accuracy(self, target_rpm, expected_tolerance):
        """After 300 frames of simulated pedaling, detected RPM should be close to target."""
        calc = simulate_pedaling(rpm_target=target_rpm, frames=300, fps=30.0)
        assert calc.phase == "TRACKING", f"Expected TRACKING phase, got {calc.phase}"
        assert abs(calc.rpm - target_rpm) <= expected_tolerance, (
            f"At {target_rpm} RPM: expected ±{expected_tolerance}, got {calc.rpm}"
        )

    def test_revolutions_counted(self):
        """At 90 RPM for 10 seconds (3s calibration + 7s tracking),
        we should have ~10-11 revolutions from the tracking phase.
        (The first 90 frames / 3 seconds go to calibration, not revolution counting.)
        """
        calc = simulate_pedaling(rpm_target=90, frames=300, fps=30.0)
        # 90 RPM × 7 sec tracking = 10.5 revolutions
        assert 7 <= calc.revolutions <= 13, (
            f"Expected ~10 revolutions at 90 RPM for 7s tracking, got {calc.revolutions}"
        )
