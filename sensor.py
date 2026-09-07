class Sensor:
    """
    Represents one physically connected sensor. Holds the wiring-level facts
    only (analog vs digital, pins, protocol) - it does NOT know what model
    it is until a SensorProfile is attached via apply_profile().

    Use Sensor.analog(...) or Sensor.digital(...) to create one - this is
    Python's equivalent of the two constructors in the C# diagram, since
    Python doesn't support having two real __init__ methods.
    """

    def __init__(self, name, is_analog, **kwargs):
        self.name = name
        self.is_analog = is_analog

        # Analog-specific wiring facts
        self.gnd = kwargs.get("gnd")
        self.vcc = kwargs.get("vcc")
        self.pincount = kwargs.get("pincount")

        # Digital-specific wiring facts
        self.family = kwargs.get("family")
        self.familycode = kwargs.get("familycode")
        self.protocol = kwargs.get("protocol")

        # Filled in later, once identified
        self.profile = None
        self.device_address = kwargs.get("device_address")
        self.gpio_ports = kwargs.get("gpio_ports", [])   # which port(s) this sensor occupies

    @classmethod
    def analog(cls, name, gnd, vcc, pincount, **kwargs):
        return cls(name, is_analog=True, gnd=gnd, vcc=vcc, pincount=pincount, **kwargs)

    @classmethod
    def digital(cls, name, family, familycode, protocol, **kwargs):
        return cls(name, is_analog=False, family=family, familycode=familycode,
                   protocol=protocol, **kwargs)

    def apply_profile(self, profile):
        """Attaches a SensorProfile once the sensor's model has been identified."""
        self.profile = profile

    def read_value(self):
        """
        Reads current values using the attached profile's read function.
        Returns a dict like {"temperature": 23.5} or {"temperature": 23.5, "humidity": 45.0},
        or None if no profile is attached yet, or the read failed.
        """
        if self.profile is None:
            return None
        return self.profile.read_fn(self.device_address)

    def __repr__(self):
        model = self.profile.model if self.profile else "unidentified"
        return f"Sensor({model}, {'Analog' if self.is_analog else 'Digital'})"