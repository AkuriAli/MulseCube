import time

try:
    import smbus2
except ImportError:
    smbus2 = None

I2C_BUS_NUMBER = 1
ADDRESS = 0x53

# ---- Registers (LTR-390UV-01 datasheet / Linux kernel driver) ----
MAIN_CTRL = 0x00      # bit 1 = measurement enable, bit 3 = UV mode (0 = ambient light)
MEAS_RATE = 0x04      # bits 6:4 = resolution / integration time, bits 2:0 = measurement rate
GAIN = 0x05           # bits 2:0 = gain
PART_ID = 0x06        # upper nibble = part number (0xB)
MAIN_STATUS = 0x07    # bit 3 = new data available
ALS_DATA = 0x0D       # 3 bytes, little-endian, 20 bits
UVS_DATA = 0x10       # 3 bytes, little-endian, 20 bits

LS_ENABLE = 0x02
UV_MODE = 0x08
NEW_DATA = 0x08
PART_NUMBER = 0x0B

GAIN_CODES = {1: 0, 3: 1, 6: 2, 9: 3, 18: 4}

# resolution code -> (integration time in ms, resolution in bits)
RESOLUTIONS = {0: (400, 20), 1: (200, 19), 2: (100, 18), 3: (50, 17), 4: (25, 16), 5: (12.5, 13)}

# measurement-rate code -> period in ms. The rate must not be shorter than the integration time.
RATE_PERIODS = [(0, 25), (1, 50), (2, 100), (3, 200), (4, 500), (5, 1000), (6, 2000)]

# Ambient light: the factory-calibrated setting is gain 3. If bright light saturates
# it, step down to gain 1, which reaches about 157,000 lux at 18-bit.
ALS_RESOLUTION = 2
ALS_GAINS = (3, 1)

# UV: the datasheet only gives an accurate conversion for 18x gain at 20-bit
# resolution (2300 counts per UV index), so that exact setting is used.
UV_RESOLUTION = 0
UV_GAIN = 18
UV_COUNTS_PER_UVI = 2300

# Window factor: 1.0 for a bare sensor. A sensor behind a window or cover sees less
# light; set this above 1 (from a calibration against a reference) to compensate.
WINDOW_FACTOR = 1.0

_last_problem = None


def _note_problem(message):
    """Print a problem once; stay quiet while it persists, report again if it comes back."""
    global _last_problem
    if message is None:
        _last_problem = None
    elif message != _last_problem:
        _last_problem = message
        print(f"  LTR390: {message}")


def _rate_code(integration_ms):
    for code, period in RATE_PERIODS:
        if period >= integration_ms:
            return code
    return RATE_PERIODS[-1][0]


def _measure(bus, uv_mode, gain, resolution_code):
    """
    Runs one measurement and returns (raw counts, resolution bits, integration ms).
    The sensor is put in standby while it is configured, then enabled, which starts
    a fresh measurement - so the data read afterwards belongs to this configuration.
    """
    integration_ms, bits = RESOLUTIONS[resolution_code]

    bus.write_byte_data(ADDRESS, MAIN_CTRL, 0x00)
    bus.write_byte_data(ADDRESS, MEAS_RATE, (resolution_code << 4) | _rate_code(integration_ms))
    bus.write_byte_data(ADDRESS, GAIN, GAIN_CODES[gain])
    bus.write_byte_data(ADDRESS, MAIN_CTRL, LS_ENABLE | (UV_MODE if uv_mode else 0))

    time.sleep(integration_ms / 1000.0 + 0.05)

    # Normally the data is ready by now; allow a little longer if it isn't.
    deadline = time.time() + 0.25
    while time.time() < deadline:
        if bus.read_byte_data(ADDRESS, MAIN_STATUS) & NEW_DATA:
            break
        time.sleep(0.02)

    data = bus.read_i2c_block_data(ADDRESS, UVS_DATA if uv_mode else ALS_DATA, 3)
    raw = (data[0] | (data[1] << 8) | (data[2] << 16)) & 0x0FFFFF
    return raw, bits, integration_ms


def _read_lux(bus):
    for gain in ALS_GAINS:
        raw, bits, integration_ms = _measure(bus, False, gain, ALS_RESOLUTION)
        if raw < (1 << bits) - 1:       # not saturated
            break
    return 0.6 * raw / (gain * (integration_ms / 100.0)) * WINDOW_FACTOR


def _read_uvi(bus):
    raw, _bits, integration_ms = _measure(bus, True, UV_GAIN, UV_RESOLUTION)
    sensitivity = UV_COUNTS_PER_UVI * (UV_GAIN / 18.0) * (integration_ms / 400.0)
    return raw / sensitivity * WINDOW_FACTOR


def read_raw(device_address=None):
    """
    Reads (UV index, illuminance in lux). Returns (None, None) if the sensor can't
    be read. A full reading takes roughly half a second, because the sensor can only
    measure UV or ambient light at one time. device_address is accepted for
    consistency with the other drivers; the LTR390 is always at 0x53.
    """
    if smbus2 is None:
        _note_problem("smbus2 is not installed - run: sudo pip install smbus2 --break-system-packages")
        return None, None

    bus = None
    try:
        bus = smbus2.SMBus(I2C_BUS_NUMBER)

        part_id = bus.read_byte_data(ADDRESS, PART_ID)
        if (part_id >> 4) != PART_NUMBER:
            _note_problem(f"the device at 0x53 isn't an LTR390 (part id 0x{part_id:02X})")
            return None, None

        lux = _read_lux(bus)
        uvi = _read_uvi(bus)
        bus.write_byte_data(ADDRESS, MAIN_CTRL, 0x00)      # back to standby between readings
    except OSError as e:
        _note_problem(f"I2C read failed: {e}")
        return None, None
    finally:
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass

    _note_problem(None)
    return round(uvi, 2), round(lux, 1)
