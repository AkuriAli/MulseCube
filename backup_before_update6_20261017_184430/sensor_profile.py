class SensorProfile:
    """
    The "identity card" for a specific sensor model - e.g. "this is a DS18B20,
    it's found via its 1-Wire family code, here's how to read it, and here's
    what it can measure." A Sensor object looks one of these up after it's
    been built, rather than knowing this information itself.
    """

    def __init__(self, model, protocol, is_analog, identifier_type, identifier,
                 read_fn, measurements, resolve_address_fn=None, release_fn=None,
                 works_without_pin=False):
        self.model = model
        self.protocol = protocol
        self.is_analog = is_analog          # which Sensor constructor this model uses
        self.identifier_type = identifier_type  # "family_code" | "i2c_address" | "manual_gpio"
        self.identifier = identifier            # e.g. "28", "0x76", or a GPIO pin number
        self.read_fn = read_fn              # function that returns {measurement_type: raw_value}
        self.measurements = measurements    # list[SensorMeasurement]
        # How to turn a raw GPIO pin number into whatever this model's read_fn
        # actually needs. Defaults to "just use the pin number as-is" (fine for
        # simple digital sensors like MQ-8). DHT11/DHT22 override this to load
        # their kernel overlay and return a resolved device path instead.
        self.resolve_address_fn = resolve_address_fn or (lambda pin: pin)
        # How to let go of a pin when this sensor is unplugged (e.g. remove the
        # kernel overlay, close the GPIO handle) so the pin is free for the next
        # sensor. Defaults to doing nothing.
        self.release_fn = release_fn or (lambda pin: None)
        # True if the sensor can still give useful readings with no GPIO pin wired
        # (an MQ-8 whose DO wire isn't connected still has its analog reading).
        self.works_without_pin = works_without_pin

    def get_measurement(self, measurement_type):
        """Finds this profile's SensorMeasurement for a given type (e.g. 'temperature')."""
        for m in self.measurements:
            if m.type == measurement_type:
                return m
        return None

    def __repr__(self):
        return f"SensorProfile({self.model}, {self.protocol})"
