from gpiozero import InputDevice

_pin_devices = {}   # gpio_pin -> InputDevice, kept open across reads for efficiency


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


def has_open_pins():
    """True while any pin is being held open for continuous reading."""
    return bool(_pin_devices)
