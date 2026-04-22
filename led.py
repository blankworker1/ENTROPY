"""
led.py — Onboard ACT LED control for Proof of Work/Wear node
Controls the Pi Zero 2 W's onboard green ACT LED via sysfs.
All status communication is through flash patterns (single colour).
Must be run as root or with appropriate sysfs permissions.

Patterns:
  SYNCED      — solid on         (healthy, peers connected)
  SYNCING     — slow pulse       (online, catching up)
  SOLO        — double blink     (no peers reachable)
  BOOT        — three rapid      (startup confirmation)
  CHAIN_ERROR — SOS pattern      (integrity failure — act immediately)
"""

import os
import time
import threading
import logging

log = logging.getLogger("led")

# ---------------------------------------------------------------------------
# Sysfs paths — Pi Zero 2 W ACT LED
# ---------------------------------------------------------------------------

LED_PATH       = "/sys/class/leds/ACT"
LED_BRIGHTNESS = f"{LED_PATH}/brightness"
LED_TRIGGER    = f"{LED_PATH}/trigger"


# ---------------------------------------------------------------------------
# Low-level control
# ---------------------------------------------------------------------------

def _write(path: str, value: str) -> bool:
    """Write to a sysfs file. Returns False if unavailable (e.g. not on Pi)."""
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except (PermissionError, FileNotFoundError, OSError):
        return False


def _set_trigger(trigger: str) -> bool:
    """Set LED trigger mode. 'none' = manual control via brightness."""
    return _write(LED_TRIGGER, trigger)


def _on() -> bool:
    return _write(LED_BRIGHTNESS, "1")


def _off() -> bool:
    return _write(LED_BRIGHTNESS, "0")


def _available() -> bool:
    """Check whether sysfs LED control is available."""
    return os.path.exists(LED_BRIGHTNESS)


# ---------------------------------------------------------------------------
# Pattern primitives
# ---------------------------------------------------------------------------

def _blink(on_ms: int, off_ms: int, count: int = 1) -> None:
    """Blink the LED count times with given on/off durations in milliseconds."""
    for _ in range(count):
        _on()
        time.sleep(on_ms / 1000)
        _off()
        time.sleep(off_ms / 1000)


def _dot() -> None:
    """Morse dot — 100ms on."""
    _blink(100, 100)


def _dash() -> None:
    """Morse dash — 300ms on."""
    _blink(300, 100)


# ---------------------------------------------------------------------------
# Named patterns
# ---------------------------------------------------------------------------

def pattern_boot() -> None:
    """Three rapid blinks — startup confirmation."""
    _blink(80, 80, count=3)
    time.sleep(0.5)


def pattern_synced() -> None:
    """Solid on — chain healthy, peers connected."""
    _on()


def pattern_syncing_step() -> None:
    """One slow pulse step — call in a loop for continuous syncing animation."""
    _on()
    time.sleep(1.0)
    _off()
    time.sleep(1.0)


def pattern_solo_step() -> None:
    """Double blink then pause — no peers reachable."""
    _blink(120, 120, count=2)
    time.sleep(2.0)


def pattern_chain_error_step() -> None:
    """
    SOS pattern — chain integrity failure.
    ... --- ...
    Three dots, three dashes, three dots, long pause.
    This state requires immediate custodian attention.
    """
    # S — three dots
    _dot(); _dot(); _dot()
    time.sleep(0.3)
    # O — three dashes
    _dash(); _dash(); _dash()
    time.sleep(0.3)
    # S — three dots
    _dot(); _dot(); _dot()
    # Long pause between repetitions
    time.sleep(3.0)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class NodeState:
    BOOT        = "boot"
    SYNCED      = "synced"
    SYNCING     = "syncing"
    SOLO        = "solo"
    CHAIN_ERROR = "chain_error"


class LEDController(threading.Thread):
    """
    Background thread that drives the ACT LED based on node state.
    The sync daemon and chain verifier update the state;
    this thread translates state into the correct flash pattern.

    Usage:
        led = LEDController()
        led.start()
        led.set_state(NodeState.SYNCED)
        ...
        led.stop()
    """

    def __init__(self):
        super().__init__(daemon=True, name="LEDController")
        self._state    = NodeState.BOOT
        self._stop_evt = threading.Event()
        self._lock     = threading.Lock()
        self._available = _available()

        if not self._available:
            log.warning("ACT LED sysfs not available — running in simulation mode")

    def set_state(self, state: str) -> None:
        with self._lock:
            if state != self._state:
                log.info(f"LED state: {self._state} → {state}")
                self._state = state

    def get_state(self) -> str:
        with self._lock:
            return self._state

    def stop(self) -> None:
        self._stop_evt.set()
        _off()

    def run(self) -> None:
        if not self._available:
            self._run_simulation()
            return

        # Take manual control of the LED
        _set_trigger("none")
        _off()

        # Boot sequence
        pattern_boot()
        self.set_state(NodeState.SOLO)

        while not self._stop_evt.is_set():
            state = self.get_state()

            if state == NodeState.SYNCED:
                pattern_synced()
                # Hold solid; check for state change every 2s
                self._stop_evt.wait(2.0)

            elif state == NodeState.SYNCING:
                pattern_syncing_step()

            elif state == NodeState.SOLO:
                pattern_solo_step()

            elif state == NodeState.CHAIN_ERROR:
                pattern_chain_error_step()

            else:
                # Unknown state — slow blink as fallback
                _blink(500, 500)

        # Restore mmc0 trigger on shutdown (default Pi LED behaviour)
        _set_trigger("mmc0")
        log.info("LED controller stopped, trigger restored to mmc0")

    def _run_simulation(self) -> None:
        """Log state changes when hardware is unavailable (development mode)."""
        last_state = None
        while not self._stop_evt.is_set():
            state = self.get_state()
            if state != last_state:
                log.info(f"[LED SIM] {state}")
                last_state = state
            self._stop_evt.wait(2.0)


# ---------------------------------------------------------------------------
# Integration with sync daemon
# ---------------------------------------------------------------------------

_controller: LEDController | None = None


def start() -> LEDController:
    """Start the LED controller thread. Call once on node startup."""
    global _controller
    _controller = LEDController()
    _controller.start()
    return _controller


def set_state(state: str) -> None:
    """Update LED state from anywhere in the codebase."""
    if _controller:
        _controller.set_state(state)


def get_state() -> str:
    if _controller:
        return _controller.get_state()
    return "unknown"


# ---------------------------------------------------------------------------
# Convenience functions called by sync daemon
# ---------------------------------------------------------------------------

def on_sync_complete(chain_valid: bool, peers_connected: bool) -> None:
    """
    Called by the sync daemon after each sync cycle.
    Translates sync outcome to LED state.
    """
    if not chain_valid:
        set_state(NodeState.CHAIN_ERROR)
    elif peers_connected:
        set_state(NodeState.SYNCED)
    else:
        set_state(NodeState.SOLO)


def on_syncing() -> None:
    """Called when a sync cycle begins and events are being fetched."""
    set_state(NodeState.SYNCING)


# ---------------------------------------------------------------------------
# Standalone test — run directly on the Pi to verify LED behaviour
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("Proof of Work/Wear — LED pattern test")
    print("======================================")
    print(f"LED available: {_available()}")
    print()

    if not _available():
        print("Not running on Pi — simulating pattern timing only")
        print()

    patterns = [
        ("Boot confirmation (3 rapid blinks)", pattern_boot),
        ("Syncing (slow pulse × 3)", lambda: [pattern_syncing_step() for _ in range(3)]),
        ("Solo / no peers (double blink × 3)", lambda: [pattern_solo_step() for _ in range(3)]),
        ("Chain error — SOS (2 repetitions)", lambda: [pattern_chain_error_step() for _ in range(2)]),
    ]

    for name, fn in patterns:
        print(f"  {name}")
        if _available():
            _set_trigger("none")
            fn()
            _off()
        else:
            print(f"    [simulation — timing only]")
            time.sleep(0.5)
        print()

    if _available():
        print("  Solid on (SYNCED state) — 3 seconds")
        _set_trigger("none")
        pattern_synced()
        time.sleep(3)
        _off()
        _set_trigger("mmc0")
        print()

    print("Test complete.")
