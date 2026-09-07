from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import ds18b20_driver, dht_driver


def _read_ds18b20(device_address):
    temp = ds18b20_driver.read_raw(device_address)
    if temp is None:
        return None
    return {"temperature": temp}


def _read_dht(device_address):
    """
    Shared by DHT11 and DHT22 - device_address here is the already-resolved
    sysfs device path (set once per Sensor instance when the user confirms
    which port it's on), so this function doesn't need to know or care
    which GPIO pin it came from.
    """
    temperature, humidity = dht_driver.read_raw(device_address)
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
        is_analog=True,
        identifier_type="manual_gpio",
        identifier=None,          # assigned per-instance at detection time, not fixed
        read_fn=_read_dht,
        measurements=[
            SensorMeasurement("temperature", "Cel", 0, 50),
            SensorMeasurement("humidity", "%RH", 20, 90),
        ],
    ),
    SensorProfile(
        model="DHT22",
        protocol="GPIO-Timing",
        is_analog=True,
        identifier_type="manual_gpio",
        identifier=None,          # assigned per-instance at detection time, not fixed
        read_fn=_read_dht,
        measurements=[
            SensorMeasurement("temperature", "Cel", -40, 80),
            SensorMeasurement("humidity", "%RH", 0, 100),
        ],
    ),
]


def find_by_family_code(family_code):
    for profile in PROFILES:
        if profile.identifier_type == "family_code" and profile.identifier == family_code:
            return profile
    return None


def find_by_i2c_address(address):
    for profile in PROFILES:
        if profile.identifier_type == "i2c_address" and profile.identifier == address:
            return profile
    return None


def get_manual_registration_profiles():
    return [p for p in PROFILES if p.identifier_type == "manual_gpio"]


def get_all_profiles():
    return PROFILES