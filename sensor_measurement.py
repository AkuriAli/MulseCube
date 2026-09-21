class SensorMeasurement:
    """
    Describes ONE thing a sensor model can measure - e.g. "Temperature, in
    Celsius, valid between -55 and 125". A single SensorProfile can hold
    several of these (e.g. DHT11 has both a temperature and a humidity
    SensorMeasurement).
    """

    def __init__(self, measurement_type, unit, min_value, max_value):
        self.type = measurement_type
        self.unit = unit
        self.min_value = min_value
        self.max_value = max_value

    def is_within_range(self, value):
        """Returns True if a given reading falls within this measurement's expected range."""
        return self.min_value <= value <= self.max_value

    def __repr__(self):
        return f"SensorMeasurement({self.type}, {self.unit}, {self.min_value}-{self.max_value})"