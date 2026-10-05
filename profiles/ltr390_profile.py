from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import ltr390_driver


def _read(device_address):
    uv_index, lux = ltr390_driver.read_raw(device_address)
    if uv_index is None or lux is None:
        return None
    return {"uv_index": uv_index, "illuminance": lux}


PROFILE = SensorProfile(
    model="LTR390",
    protocol="I2C",
    is_analog=False,
    identifier_type="i2c_address",
    identifier=0x53,
    read_fn=_read,
    measurements=[
        # UV index: the scale runs 0-11, with 11+ meaning "extreme"
        SensorMeasurement("uv_index", "UVI", 0, 11),
        # Ambient light: the datasheet's linear range, 0.01 lux up to 157k lux
        SensorMeasurement("illuminance", "lx", 0, 157000),
    ],
)
