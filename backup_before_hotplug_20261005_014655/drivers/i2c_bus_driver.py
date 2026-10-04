try:
    import smbus2
except ImportError:
    smbus2 = None

I2C_BUS_NUMBER = 1


def scan_i2c_bus():
    """
    Scans the I2C bus for responding device addresses (0x03-0x77, the
    conventional valid range). Returns a list of addresses found, as integers.

    Returns an empty list - never raises - if the smbus2 library is missing
    or the I2C bus is switched off. "No I2C sensors found" must never stop
    the rest of sensor detection (1-Wire, GPIO) from running.
    """
    if smbus2 is None:
        print("  (I2C scan skipped: smbus2 is not installed)")
        return []

    try:
        bus = smbus2.SMBus(I2C_BUS_NUMBER)
    except OSError:
        print("  (I2C scan skipped: I2C bus not available - "
              "enable it in raspi-config if you use I2C sensors)")
        return []

    found = []
    for address in range(0x03, 0x78):
        try:
            bus.read_byte(address)
            found.append(address)
        except OSError:
            continue

    bus.close()
    return found