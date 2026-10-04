import time
from gpiozero import InputDevice, Device

# Pins already claimed by dedicated protocols - never scan these generically,
# since I2C/1-Wire detection already covers them.
RESERVED_PINS = {2, 3, 4, 14, 15}   # I2C (2,3), 1-Wire (4), UART (14,15)

# Common "free" GPIOs on a standard 40-pin header, excluding reserved ones.
CANDIDATE_PINS = [5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]

SETTLE_DELAY = 0.05      # let the line settle before trusting any reading
SAMPLES_PER_TEST = 3
SAMPLE_DELAY = 0.01

_reported_errors = set()


def _stable_read(pin):
    """
    Reads one pin with the internal pull-down enabled, taking several samples.
    Returns the value only if every sample agrees (filters electrical noise);
    returns None if the readings were inconsistent. Always releases the pin.
    """
    device = InputDevice(pin, pull_up=False)
    try:
        time.sleep(SETTLE_DELAY)
        readings = []
        for _ in range(SAMPLES_PER_TEST):
            readings.append(device.value)
            time.sleep(SAMPLE_DELAY)
    finally:
        device.close()

    if all(r == readings[0] for r in readings):
        return readings[0]
    return None


def scan_pins(pins):
    """
    Checks the given pins. Returns {pin: True} for a stable HIGH (something is
    plugged in), {pin: False} for a stable LOW (nothing there). Pins with a
    noisy reading, or that raised an error, are left out of the result.

    Errors are printed (once per distinct error) rather than swallowed - a
    silent failure here looks exactly like "nothing is plugged in".
    """
    result = {}
    for pin in pins:
        try:
            reading = _stable_read(pin)
        except Exception as e:
            message = f"{type(e).__name__}: {e}"
            if (pin, message) not in _reported_errors:
                _reported_errors.add((pin, message))
                print(f"  GPIO scan problem on pin {pin}: {message}")
            continue

        if reading is None:
            continue
        result[pin] = bool(reading)

    return result


def scan_gpio_connections():
    """Every candidate pin currently reading HIGH. Handy for quick diagnostics."""
    return [pin for pin, high in scan_pins(CANDIDATE_PINS).items() if high]


def release_idle_factory():
    """
    Closes gpiozero's shared pin connection - but only if no pin is being held
    open for continuous reading (e.g. an MQ-8's DO pin). Used as a fallback when
    the kernel can't claim a pin that this process touched. Returns True if the
    connection was closed. (gpiozero keeps closed pins cached, so its own pin
    table can't be used to tell whether anything is still live.)
    """
    try:
        from drivers import digital_output_driver
        if digital_output_driver.has_open_pins():
            return False
    except Exception:
        pass

    if Device.pin_factory is not None:
        Device.pin_factory.close()
        Device.pin_factory = None   # gpiozero builds a fresh one on next use
    return True
