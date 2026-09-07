from gpiozero import InputDevice

# Pins already claimed by dedicated protocols - never scan these generically,
# since I2C/1-Wire auto-detection already covers them.
RESERVED_PINS = {2, 3, 4, 14, 15}   # I2C (2,3), 1-Wire (4), UART (14,15)

# Common "free" GPIOs on a standard 40-pin header, excluding reserved ones.
CANDIDATE_PINS = [5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]


def scan_gpio_connections():
    """
    Checks each candidate pin with its internal pull-down enabled. Sensors
    like DHT11/DHT22 idle HIGH thanks to their own external pull-up resistor,
    so a pin reading HIGH here is a strong signal something is wired to it.
    A floating (unconnected) pin will read LOW.
    Returns a list of GPIO pin numbers that appear connected.
    """
    connected = []

    for pin in CANDIDATE_PINS:
        try:
            device = InputDevice(pin, pull_up=False)
            if device.value:
                connected.append(pin)
            device.close()
        except Exception:
            # Pin might be busy/unavailable - skip it rather than crash the scan
            continue

    return connected