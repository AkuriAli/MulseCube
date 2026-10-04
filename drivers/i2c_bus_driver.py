try:
    import smbus2
except ImportError:
    smbus2 = None

I2C_BUS_NUMBER = 1

_warned = set()


def _warn_once(message):
    """Print each distinct warning a single time - this runs every cycle."""
    if message not in _warned:
        _warned.add(message)
        print(f"  {message}")


def _open_bus():
    """Returns an open I2C bus, or None (with a one-time warning) if unavailable."""
    if smbus2 is None:
        _warn_once("(I2C skipped: smbus2 is not installed - "
                   "run: sudo pip install smbus2 --break-system-packages)")
        return None
    try:
        return smbus2.SMBus(I2C_BUS_NUMBER)
    except OSError:
        _warn_once("(I2C skipped: I2C bus not available - "
                   "enable it in raspi-config if you use I2C sensors)")
        return None


def probe(addresses):
    """
    Returns the subset of `addresses` that currently respond on the I2C bus.
    Much cheaper than a full scan, so it can run every cycle for hot-plug.
    Never raises: a missing library or disabled bus just means "none found".
    """
    bus = _open_bus()
    if bus is None:
        return set()

    present = set()
    for address in addresses:
        try:
            bus.read_byte(address)
            present.add(address)
        except OSError:
            continue

    bus.close()
    return present


def scan_i2c_bus():
    """
    Full scan of the conventional valid address range (0x03-0x77). Returns a
    list of responding addresses. Never raises - useful for diagnostics.
    """
    bus = _open_bus()
    if bus is None:
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
