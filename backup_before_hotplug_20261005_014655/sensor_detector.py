from sensor import Sensor
from profile_registry import find_by_family_code, find_by_i2c_address, get_manual_registration_profiles, get_all_profiles
from drivers import ds18b20_driver, i2c_bus_driver
from gpio_scanner import CANDIDATE_PINS
from remote_selector import request_selection

SKIP_OPTION = "Skip this port"
CANCEL_OPTION = "Cancel"


def auto_detect_sensors():
    """
    Scans every protocol capable of self-identification and builds a Sensor
    object (with its profile already attached) for each match found.
    """
    identified = []

    for device_address in ds18b20_driver.scan_for_ds18b20():
        family_code = device_address.split("-")[0]
        profile = find_by_family_code(family_code)

        if profile:
            sensor = Sensor.digital(
                name=profile.model,
                family=profile.model,
                familycode=family_code,
                protocol=profile.protocol,
                device_address=device_address,
            )
            sensor.apply_profile(profile)
            identified.append(sensor)

    # --- I2C scan ---
    for address in i2c_bus_driver.scan_i2c_bus():
        profile = find_by_i2c_address(address)

        if profile:
            sensor = Sensor.digital(
                name=profile.model,
                family=profile.model,
                familycode=hex(address),
                protocol=profile.protocol,
                device_address=address,
            )
            sensor.apply_profile(profile)
            identified.append(sensor)

    return identified


def _build_sensor_for_pin(profile, pin):
    """Builds a Sensor for a manually-confirmed profile on a specific GPIO pin."""
    if profile.is_analog:
        sensor = Sensor.analog(name=profile.model, gnd=None, vcc=None, pincount=1)
    else:
        sensor = Sensor.digital(name=profile.model, family=profile.model,
                                familycode="", protocol=profile.protocol)

    device_path = profile.resolve_address_fn(pin)
    sensor.device_address = device_path
    sensor.gpio_ports = [pin]
    sensor.apply_profile(profile)
    return sensor


def _label(profile):
    return f"{profile.model} ({profile.protocol})"


def prompt_for_gpio_sensors(detected_pins):
    """
    Walks through each GPIO pin that showed a physical connection, asking
    (via the HMI dashboard) which sensor is wired there. Offers "same as
    previous port" for a single sensor that spans more than one GPIO pin.
    """
    profiles = get_manual_registration_profiles()
    sensors = []
    last_sensor = None

    for pin in detected_pins:
        options = [_label(p) for p in profiles]
        if last_sensor:
            options.append(f"Same as previous port ({last_sensor.profile.model})")
        options.append(SKIP_OPTION)

        choice = request_selection(
            request_id=f"gpio-port-{pin}",
            prompt=f"Connection detected on GPIO{pin}. Which sensor is this?",
            options=options,
        )

        if choice is None or choice == SKIP_OPTION:
            continue

        if last_sensor and choice.startswith("Same as previous port"):
            last_sensor.gpio_ports.append(pin)
            continue

        matched_profile = next((p for p in profiles if _label(p) == choice), None)
        if matched_profile:
            sensor = _build_sensor_for_pin(matched_profile, pin)
            sensors.append(sensor)
            last_sensor = sensor

    return sensors


def manual_fallback_selection():
    """
    Last-resort manual add, for anything the GPIO presence scan might miss
    (e.g. a sensor that idles LOW instead of HIGH).
    """
    profiles = get_all_profiles()
    options = [_label(p) for p in profiles] + [CANCEL_OPTION]

    choice = request_selection(
        request_id="manual-fallback",
        prompt="Add a sensor manually:",
        options=options,
    )

    if choice is None or choice == CANCEL_OPTION:
        return None

    matched_profile = next((p for p in profiles if _label(p) == choice), None)
    if not matched_profile:
        return None

    if matched_profile.identifier_type == "manual_gpio":
        pin_options = [str(p) for p in CANDIDATE_PINS]
        pin_choice = request_selection(
            request_id="manual-fallback-pin",
            prompt=f"Which GPIO pin is the {matched_profile.model}'s data/DO pin connected to?",
            options=pin_options,
        )
        if pin_choice is None:
            return None
        return _build_sensor_for_pin(matched_profile, int(pin_choice))

    # Non-GPIO sensors (e.g. future I2C models) don't need a pin at all
    if matched_profile.is_analog:
        sensor = Sensor.analog(name=matched_profile.model, gnd=None, vcc=None, pincount=1)
    else:
        sensor = Sensor.digital(name=matched_profile.model, family=matched_profile.model,
                                familycode="", protocol=matched_profile.protocol)
    sensor.apply_profile(matched_profile)
    return sensor