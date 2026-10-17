"""
Small building blocks used by the sensor monitor: turning "something was found"
into a Sensor object, and the dashboard questions for manually-added sensors.
"""
from sensor import Sensor
from profile_registry import get_manual_registration_profiles, get_one_wire_profiles
from drivers import ds18b20_driver
import remote_selector

SKIP_OPTION = "Skip this port"
CANCEL_OPTION = "Cancel"
NO_PIN_OPTION = "No DO wire - use the analog reading only"


def model_label(profile):
    return f"{profile.model} ({profile.protocol})"


def one_wire_label(profile):
    """How a 1-Wire sensor appears in the lists. It can't be set up from them - see one_wire_help."""
    return f"{profile.model} (1-Wire - GPIO4 only)"


def one_wire_help(profile, pin=None):
    """What to tell someone who picked a 1-Wire sensor from a list."""
    where = f"GPIO{pin}" if pin is not None else "an ordinary pin"
    text = (f"The {profile.model} is a 1-Wire sensor, so it can't be set up on {where} - and "
            f"it never needs to be picked from this list, because it identifies itself. "
            f"Wire its DATA line to GPIO4 (physical pin 7), plus 3.3 V and ground, with a "
            f"4.7 kOhm resistor between DATA and 3.3 V. It then appears on the dashboard by "
            f"itself. To use a different pin, add the line dtoverlay=w1-gpio,gpiopin=N to "
            f"/boot/firmware/config.txt and reboot.")
    if not ds18b20_driver.one_wire_enabled():
        text += (" 1-Wire is also still switched off: run 'sudo raspi-config', choose "
                 "Interface Options, then 1-Wire, enable it, and reboot.")
    return text


def build_1wire_sensor(profile, device_address):
    sensor = Sensor.digital(
        name=profile.model,
        family=profile.model,
        familycode=device_address.split("-")[0],
        protocol=profile.protocol,
        device_address=device_address,
    )
    sensor.apply_profile(profile)
    return sensor


def build_i2c_sensor(profile, address):
    sensor = Sensor.digital(
        name=profile.model,
        family=profile.model,
        familycode=hex(address),
        protocol=profile.protocol,
        device_address=address,
    )
    sensor.apply_profile(profile)
    return sensor


def build_gpio_sensor(profile, pin):
    """
    Sets up a sensor on a GPIO pin. Returns None if the pin couldn't be set up
    (for a DHT that means the kernel overlay failed to load).
    """
    if pin is None:
        if not profile.works_without_pin:
            return None
        address = None
    else:
        address = profile.resolve_address_fn(pin)
        if address is None:
            return None

    if profile.is_analog:
        sensor = Sensor.analog(name=profile.model, gnd=None, vcc=None, pincount=1)
    else:
        sensor = Sensor.digital(name=profile.model, family=profile.model,
                                familycode="", protocol=profile.protocol)

    sensor.device_address = address
    sensor.gpio_ports = [pin] if pin is not None else []
    sensor.apply_profile(profile)
    return sensor


def choose_manual_sensor(free_pins):
    """
    Asks, on the dashboard, which model to add and which GPIO pin it is on.
    Blocks until answered, so call it from a worker thread. Returns
    (profile, pin), or None if cancelled.
    """
    profiles = get_manual_registration_profiles()
    one_wire = get_one_wire_profiles()
    options = ([model_label(p) for p in profiles]
               + [one_wire_label(p) for p in one_wire]
               + [CANCEL_OPTION])

    choice = remote_selector.request_selection(
        request_id="manual-model",
        prompt="Add a sensor manually - which model?",
        options=options,
    )
    if choice is None or choice == CANCEL_OPTION:
        return None

    chosen_one_wire = next((p for p in one_wire if one_wire_label(p) == choice), None)
    if chosen_one_wire is not None:
        return ("one_wire", chosen_one_wire)       # nothing to set up - the caller explains

    profile = next((p for p in profiles if model_label(p) == choice), None)
    if profile is None:
        return None

    pin_options = [str(p) for p in free_pins] + [CANCEL_OPTION]
    if profile.works_without_pin:
        pin_options = [NO_PIN_OPTION] + pin_options

    pin_choice = remote_selector.request_selection(
        request_id="manual-pin",
        prompt=f"Which GPIO pin is the {profile.model}'s data/DO pin connected to?",
        options=pin_options,
    )
    if pin_choice is None or pin_choice == CANCEL_OPTION:
        return None
    if pin_choice == NO_PIN_OPTION:
        return profile, None

    return profile, int(pin_choice)
