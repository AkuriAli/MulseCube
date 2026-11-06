import time

from gpiozero import InputDevice

_pin_devices = {}   # gpio_pin -> InputDevice, kept open across reads for efficiency
_presence_devices = {}   # presence pin -> InputDevice with the Pi's internal pull-up switched on
_reported_errors = set()


def read_raw(gpio_pin):
    """
    Reads a simple HIGH/LOW digital threshold output - e.g. an MQ-series gas
    sensor's DO pin, which just says "above/below threshold", not a real
    protocol. Returns 1 if triggered, 0 if not, or None on error.
    """
    try:
        if gpio_pin not in _pin_devices:
            _pin_devices[gpio_pin] = InputDevice(gpio_pin, pull_up=False)
        return int(_pin_devices[gpio_pin].value)
    except Exception as e:
        print(f"  MQ-8 read error on GPIO{gpio_pin}: {e}")
        return None


def release(gpio_pin):
    """Closes and forgets the handle on this pin (called when the sensor is unplugged)."""
    device = _pin_devices.pop(gpio_pin, None)
    if device is not None:
        try:
            device.close()
        except Exception:
            pass


def read_presence(pin):
    """
    True if something is plugged into the port, False if not, None if the pin can't be
    read reliably. The plug joins the presence wire to ground, so a plugged-in port reads
    LOW, and the Pi's internal pull-up makes an empty port read HIGH.
    """
    try:
        device = _presence_devices.get(pin)
        if device is None:
            device = _presence_devices[pin] = InputDevice(pin, pull_up=True)
            time.sleep(0.05)                    # let the pull-up settle
        samples = []
        for _ in range(3):
            samples.append(bool(device.is_active))      # is_active = the pin is LOW
            time.sleep(0.01)
    except Exception as e:
        message = f"{type(e).__name__}: {e}"
        if (pin, message) not in _reported_errors:
            _reported_errors.add((pin, message))
            print(f"  Presence pin GPIO{pin} problem: {message}")
        return None
    return samples[0] if all(x == samples[0] for x in samples) else None


def release_presence_pins():
    for pin in list(_presence_devices):
        device = _presence_devices.pop(pin)
        try:
            device.close()
        except Exception:
            pass


def has_open_pins():
    """True while any pin is being held open for continuous reading."""
    return bool(_pin_devices) or bool(_presence_devices)
