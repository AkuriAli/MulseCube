from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import ds18b20_driver, dht11_driver


# Small wrapper functions so every profile's read_fn has the SAME shape:
# takes a device_address (ignored if not needed) and returns a dict of
# {measurement_type: value}, regardless of how many things the sensor measures.

def _read_ds18b20(device_address):
    temp = ds18b20_driver.read_raw(device_address)
    if temp is None:
        return None
    return {"temperature": temp}


def _read_dht11(device_address=None):
    temperature, humidity = dht11_driver.read_raw()
    if temperature is None or humidity is None:
        return None
    return {"temperature": temperature, "humidity": humidity}


PROFILES = [
    SensorProfile(
        model="DS18B20",
        protocol="1-Wire",
        is_analog=False,
        identifier_type="family_code",
        identifier="28",
        read_fn=_read_ds18b20,
        measurements=[
            SensorMeasurement("temperature", "Cel", -55, 125),
        ],
    ),
    SensorProfile(
        model="DHT11",
        protocol="GPIO-Timing",
        is_analog=True,          # per your team's simplified bucketing: non-addressable = "Analog"
        identifier_type="manual_gpio",
        identifier=17,
        read_fn=_read_dht11,
        measurements=[
            SensorMeasurement("temperature", "Cel", 0, 50),
            SensorMeasurement("humidity", "%RH", 20, 90),
        ],
    ),
]


def find_by_family_code(family_code):
    """Used during 1-Wire auto-detection."""
    for profile in PROFILES:
        if profile.identifier_type == "family_code" and profile.identifier == family_code:
            return profile
    return None


def find_by_i2c_address(address):
    """Used during I2C auto-detection (not yet implemented)."""
    for profile in PROFILES:
        if profile.identifier_type == "i2c_address" and profile.identifier == address:
            return profile
    return None


def get_manual_registration_profiles():
    """Profiles that can NEVER be auto-detected (e.g. DHT-family) - offered as menu choices."""
    return [p for p in PROFILES if p.identifier_type == "manual_gpio"]


def get_all_profiles():
    """Used for the full manual fallback menu, when auto-detection finds nothing at all."""
    return PROFILES