import os
import re
import subprocess
import time

IIO_BASE_DIR = "/sys/bus/iio/devices"
OVERLAY_NAME = "dht11"     # the kernel's overlay for DHT11, DHT21 and DHT22

# Only used if the pin-based lookup below can't find a device: remembers the
# device that appeared right after we loaded the overlay for that pin.
_fallback_paths = {}

# Last failure reason printed per pin, so a steady failure is reported once
# instead of every cycle - and a sensor that recovers and fails again is reported again.
_last_failure = {}


def _run(command):
    return subprocess.run(command, capture_output=True, text=True)


def _note_failure(pin, message):
    if _last_failure.get(pin) != message:
        _last_failure[pin] = message
        print(f"  DHT on GPIO{pin}: {message}")


def _note_success(pin):
    _last_failure.pop(pin, None)


def _error_text(result):
    return (result.stderr.strip() or result.stdout.strip() or "no details given")


# ---------------------------------------------------------------- overlays --

def _list_overlays():
    """
    Returns [(index, gpio_pin_or_None)] for every dht11 overlay that was loaded
    at runtime. (dtoverlay -l only shows runtime overlays - ones written in
    config.txt can't be listed or removed without a reboot.)
    """
    try:
        result = _run(["dtoverlay", "-l"])
    except FileNotFoundError:
        return []

    found = []
    for line in result.stdout.splitlines():
        match = re.match(rf"\s*(\d+)\s*[:_]\s*{OVERLAY_NAME}\b(.*)$", line)
        if match:
            pin_match = re.search(r"gpiopin\s*=\s*(\d+)", match.group(2))
            pin = int(pin_match.group(1)) if pin_match else None
            found.append((int(match.group(1)), pin))
    return found


def cleanup_stale_overlays():
    """
    Removes every dht11 overlay left loaded from a previous run. An overlay is a
    kernel-level change: it survives the program exiting, and while it is loaded
    the kernel owns that GPIO pin, so a pin scan can't even look at it (it looks
    "unplugged"). Call once at startup, before scanning.
    """
    removed = 0
    try:
        for _ in range(10):
            overlays = _list_overlays()
            if not overlays:
                break
            index = max(i for i, _pin in overlays)     # highest first
            result = _run(["dtoverlay", "-r", str(index)])
            if result.returncode != 0:
                print(f"  WARNING: could not remove DHT overlay {index}: {_error_text(result)}")
                break
            removed += 1

        # If the listing format wasn't what we expected, fall back to removing by name
        listing = _run(["dtoverlay", "-l"]).stdout
        if OVERLAY_NAME in listing and not _list_overlays():
            for _ in range(10):
                if _run(["dtoverlay", "-r", OVERLAY_NAME]).returncode != 0:
                    break
                removed += 1
    except FileNotFoundError:
        return      # not a Raspberry Pi - nothing to clean up

    _fallback_paths.clear()
    if removed:
        print(f"  Cleared {removed} leftover DHT overlay(s) from a previous run.")


def release_device(gpio_pin):
    """Removes the overlay for one pin, handing the pin back (called on unplug)."""
    _fallback_paths.pop(gpio_pin, None)

    overlays = _list_overlays()
    for index, pin in sorted(overlays, reverse=True):
        if pin == gpio_pin:
            result = _run(["dtoverlay", "-r", str(index)])
            if result.returncode != 0:
                print(f"  WARNING: could not remove the DHT overlay for GPIO{gpio_pin}: "
                      f"{_error_text(result)}")
            return

    # Only worth mentioning if some overlay's pin couldn't be read from the listing;
    # otherwise there is simply nothing loaded for this pin, and that's fine.
    if any(pin is None for _index, pin in overlays):
        print(f"  NOTE: couldn't tell which DHT overlay belongs to GPIO{gpio_pin}; left loaded.")


def _release_gpio_handles():
    """Fallback before a retry: let go of gpiozero's shared connection if idle."""
    try:
        from gpio_scanner import release_idle_factory
        release_idle_factory()
    except Exception:
        pass


# ----------------------------------------------------------------- devices --

def _iio_devices():
    return set(os.listdir(IIO_BASE_DIR)) if os.path.isdir(IIO_BASE_DIR) else set()


def _device_path_for_pin(pin):
    """
    Finds the sysfs folder for the DHT on this pin. The kernel names each
    overlay's device 'dht11@<pin in hex>' (GPIO22 -> dht11@16, GPIO27 -> dht11@1b),
    so the pin identifies the device no matter how the iio:deviceN numbers shuffle.
    """
    marker = f"dht11@{pin:x}"
    if os.path.isdir(IIO_BASE_DIR):
        for entry in os.listdir(IIO_BASE_DIR):
            path = os.path.join(IIO_BASE_DIR, entry)
            if marker in os.path.realpath(path).split(os.sep):
                return path

    path = _fallback_paths.get(pin)
    return path if path and os.path.isdir(path) else None


def get_or_load_device(gpio_pin):
    """
    Loads the DHT overlay for this exact pin and waits for its device to appear.
    Returns the pin number (it is the sensor's address from now on), or None if
    the overlay couldn't be set up. Needs root.
    """
    if _device_path_for_pin(gpio_pin):
        return gpio_pin

    # Before the kernel takes the pin, let go of gpiozero's shared connection
    # (only if nothing is holding a pin open). This is the order that was proven
    # to work before hot-plug existed: scan, release, then load the overlay.
    _release_gpio_handles()

    for attempt in (1, 2):
        before = _iio_devices()
        try:
            result = _run(["dtoverlay", OVERLAY_NAME, f"gpiopin={gpio_pin}"])
        except FileNotFoundError:
            print("  The 'dtoverlay' command wasn't found - is this a Raspberry Pi?")
            return None

        if result.returncode == 0:
            deadline = time.time() + 3
            while time.time() < deadline:
                if _device_path_for_pin(gpio_pin):
                    return gpio_pin
                appeared = _iio_devices() - before
                if appeared:    # naming rule didn't match - remember what did appear
                    _fallback_paths[gpio_pin] = os.path.join(IIO_BASE_DIR, appeared.pop())
                    return gpio_pin
                time.sleep(0.1)
            print(f"  Overlay for GPIO{gpio_pin} loaded, but no sensor device appeared.")
        else:
            print(f"  Overlay for GPIO{gpio_pin} failed (attempt {attempt}): {_error_text(result)}")

        release_device(gpio_pin)           # don't leave a half-loaded overlay behind
        if attempt == 1:
            _release_gpio_handles()
            time.sleep(0.3)

    return None


def read_raw(device_address):
    """
    Reads (temperature in C, humidity in %RH) from the DHT on the given pin.
    Returns (None, None) if the sensor isn't there or the read failed - DHT
    sensors fail an occasional read normally, but several in a row means it's gone.
    """
    pin = device_address if isinstance(device_address, int) else None
    path = _device_path_for_pin(pin) if pin is not None else device_address

    if not path:
        if pin is not None:
            _note_failure(pin, "no sensor device exists for this pin (the overlay isn't loaded)")
        return None, None

    try:
        with open(os.path.join(path, "in_temp_input"), "r") as f:
            temp_raw = int(f.read().strip())
        with open(os.path.join(path, "in_humidityrelative_input"), "r") as f:
            humidity_raw = int(f.read().strip())
    except OSError as e:
        # e.g. "[Errno 110] Connection timed out" = the sensor never answered
        if pin is not None:
            _note_failure(pin, f"read failed: {e}")
        return None, None
    except ValueError:
        if pin is not None:
            _note_failure(pin, "read gave an unreadable value")
        return None, None

    if pin is not None:
        _note_success(pin)
    return temp_raw / 1000.0, humidity_raw / 1000.0
