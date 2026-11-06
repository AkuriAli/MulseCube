"""Reads and checks the port table in ports_config.py."""

# GPIO0/1 are the HAT identification pins, GPIO2/3 are the I2C bus, GPIO4 is the always-on 1-Wire pin.
NOT_FOR_PORTS = {0, 1, 2, 3, 4}


def load():
    """
    Returns (use_presence_pins, ports). If the table has a mistake, it is reported and
    presence mode stays off, so a typo can never stop the program from starting.
    """
    try:
        import ports_config
    except ImportError:
        return False, []

    if not getattr(ports_config, "USE_PRESENCE_PINS", False):
        return False, []

    problems = []
    ports = []
    used = {}

    for number, raw in enumerate(getattr(ports_config, "PORTS", []), start=1):
        name = str(raw.get("name") or f"Port {number}")
        port = {"name": name}
        for field in ("presence", "data", "adc_channel"):
            value = raw.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(f"{name}: '{field}' must be a whole number (it is {value!r})")
            port[field] = value
        for field in ("presence", "data"):
            pin = port[field]
            if isinstance(pin, int):
                if pin in NOT_FOR_PORTS:
                    problems.append(f"{name}: GPIO{pin} can't be used for '{field}' (reserved for I2C, 1-Wire or HAT identification)")
                elif pin in used:
                    problems.append(f"{name}: GPIO{pin} is already used by {used[pin]}")
                else:
                    used[pin] = f"{name} ({field})"
        ports.append(port)

    if not ports:
        problems.append("the PORTS list is empty")

    channels = [p["adc_channel"] for p in ports if isinstance(p["adc_channel"], int)]
    if len(channels) != len(set(channels)):
        problems.append("two ports share an adc_channel")

    if problems:
        print("Port settings (ports_config.py) have a problem, so presence pins are NOT being used:")
        for line in problems:
            print(f"  - {line}")
        return False, []

    names = ", ".join(f"{p['name']} (presence GPIO{p['presence']}, data GPIO{p['data']})" for p in ports)
    print(f"Presence pins in use: {names}")
    return True, ports
