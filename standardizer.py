#standardiser = the formatter that makes all sensor data look the same


import time


def standardize(sensor_key, profile, field_name, value):
    """
    Converts a raw driver reading into a SenML-inspired standardized record.
    See RFC 8428 for the full SenML spec — this is a simplified subset
    covering base name, unit, value, and time.
    """
    unit_map = {
        "temperature": "Cel",   # SenML standard unit code for Celsius
        "humidity": "%RH",
        "pressure": "Pa",
    }

    return {
        "bn": sensor_key,                     # base name: which sensor
        "n": field_name,                      # measurement name (e.g. "temperature")
        "u": unit_map.get(field_name, ""),    # standardized unit code
        "v": value,                           # the value itself
        "t": round(time.time(), 3),           # unix timestamp
        "protocol": profile["protocol"],      # extra context, not part of core SenML
    }