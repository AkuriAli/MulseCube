"""
Starts and stops 1-Wire buses on chosen GPIO pins while the Pi is running, so a
DS18B20 can be plugged into any port - not just GPIO4.

The Pi's 1-Wire driver listens on one pin, chosen by an overlay. Loading that
overlay for a pin (`dtoverlay w1-gpio gpiopin=<pin>`) creates a new bus there,
and any DS18B20 on it then shows up in /sys/bus/w1/devices like one on GPIO4.
(Overlays loaded at runtime can be removed again; ones written in config.txt can't.)

Usage by the monitor:  begin(pin) -> wait_for_sensors(pin) -> keep it, or release(pin).
"""
import os
import re
import subprocess
import time

from drivers import ds18b20_driver

OVERLAY_NAME = "w1-gpio"        # the Pi 5 lists it as 'w1-gpio-pi5'; the pattern below allows both
WAIT_SECONDS = 20               # how long to wait for a sensor to answer on a new bus

_buses = {}                     # pin -> name of its bus, e.g. 'w1_bus_master2'


class OneWireError(Exception):
    """The bus couldn't be started. The message is safe to show to a person."""


def _run(command):
    return subprocess.run(command, capture_output=True, text=True)


def _error_text(result):
    return result.stderr.strip() or result.stdout.strip() or "no details given"


def _release_gpio_handles():
    """The overlay needs the pin free, so let go of any handle the pin scanner holds."""
    try:
        from gpio_scanner import release_idle_factory
        release_idle_factory()
    except Exception:
        pass


def _masters():
    try:
        return {e for e in os.listdir(ds18b20_driver.W1_BASE_DIR) if e.startswith("w1_bus_master")}
    except OSError:
        return set()


def _slaves(master):
    """DS18B20 ids currently answering on one bus."""
    path = os.path.join(ds18b20_driver.W1_BASE_DIR, master, "w1_master_slaves")
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip().startswith("28-")]
    except OSError:
        return []


def _runtime_overlays():
    """[(index, pin)] for 1-Wire overlays loaded at runtime."""
    try:
        listing = _run(["dtoverlay", "-l"]).stdout
    except FileNotFoundError:
        return []
    found = []
    for line in listing.splitlines():
        match = re.match(rf"\s*(\d+)\s*[:_]\s*{OVERLAY_NAME}\S*(.*)$", line)
        if match:
            pin = re.search(r"gpiopin\s*=\s*(\d+)", match.group(2))
            found.append((int(match.group(1)), int(pin.group(1)) if pin else None))
    return found


def _remove_overlay_for_pin(pin):
    for index, overlay_pin in sorted(_runtime_overlays(), reverse=True):
        if overlay_pin == pin:
            result = _run(["dtoverlay", "-r", str(index)])
            if result.returncode != 0:
                print(f"  WARNING: could not remove the 1-Wire bus on GPIO{pin}: {_error_text(result)}")
                return False
            return True
    return True


# ------------------------------------------------------------------ public --

def owned_pins():
    return set(_buses)


def begin(pin):
    """Starts a bus on `pin`. Quick; call it from the main thread. Raises OneWireError."""
    if pin in _buses:
        return
    _release_gpio_handles()
    before = _masters()
    try:
        result = _run(["dtoverlay", OVERLAY_NAME, f"gpiopin={pin}"])
    except FileNotFoundError:
        raise OneWireError("the 'dtoverlay' tool isn't available on this system")
    if result.returncode != 0:
        raise OneWireError(_error_text(result))

    deadline = time.time() + 3
    while time.time() < deadline:
        new = _masters() - before
        if new:
            _buses[pin] = sorted(new)[0]
            return
        time.sleep(0.1)
    _remove_overlay_for_pin(pin)
    raise OneWireError("the Pi did not create a 1-Wire bus")


def wait_for_sensors(pin, timeout=None, poll=1.0):
    """Waits until a DS18B20 answers on the bus. Slow; call it from a worker thread."""
    master = _buses.get(pin)
    if master is None:
        return []
    end = time.time() + (WAIT_SECONDS if timeout is None else timeout)
    while time.time() < end:
        found = _slaves(master)
        if found:
            return found
        time.sleep(poll)
    return _slaves(master)


def has_sensors(pin):
    master = _buses.get(pin)
    return bool(master and _slaves(master))


def release(pin):
    """Stops the bus on `pin` and hands the pin back."""
    _buses.pop(pin, None)
    _release_gpio_handles()
    return _remove_overlay_for_pin(pin)


def release_all():
    for pin in list(_buses):
        release(pin)


def cleanup_stale():
    """Removes 1-Wire buses an earlier run left loaded. Any runtime one is ours."""
    _buses.clear()
    for _ in range(20):
        overlays = _runtime_overlays()
        if not overlays:
            return
        index = max(i for i, _pin in overlays)
        result = _run(["dtoverlay", "-r", str(index)])
        if result.returncode != 0:
            print(f"  WARNING: could not remove 1-Wire overlay {index}: {_error_text(result)}")
            return
