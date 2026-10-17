#!/bin/bash
# MulseCube update 7: a DS18B20 now works on ANY port pin, found automatically.
#   * When something is plugged into a port, the program first tries a 1-Wire bus on
#     that pin. A DS18B20 answers by itself - no question is asked.
#   * If nothing answers, the bus is removed and the usual "which sensor?" question
#     appears (DHT11 / DHT22 / MQ-8 / DS18B20).
#   * Unplugging the DS18B20 removes the bus and frees the pin again.
#
# Run this from inside your MulseCube folder:   bash apply_update_7.sh

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

for item in main.py sensor_monitor.py hmi_server.py drivers profiles; do
  if [ ! -e "$item" ]; then
    echo "Can't find '$item' here ($PROJECT_DIR)."
    echo "Put this script inside your MulseCube folder and run it from there."
    exit 1
  fi
done

OWNER="$(stat -c %U "$PROJECT_DIR")"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="backup_before_update7_$STAMP"

FILES=(
  "onewire_bus.py"
  "sensor_monitor.py"
  "sensor_detector.py"
  "main.py"
)

echo "Backing up your current files to $BACKUP_DIR/ ..."
for f in "${FILES[@]}"; do
  if [ -f "$f" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$f")"
    cp -p "$f" "$BACKUP_DIR/$f"
  fi
done

write_file() {
  mkdir -p "$(dirname "$1")"
  cat > "$1"
  if [ "$EUID" -eq 0 ]; then chown "$OWNER":"$OWNER" "$1"; fi
}

restore_backup() {
  echo
  echo "Restoring your original files from $BACKUP_DIR/ ..."
  for f in "${FILES[@]}"; do
    if [ -f "$BACKUP_DIR/$f" ]; then
      cp -p "$BACKUP_DIR/$f" "$f"
    else
      rm -f "$f"          # a file this update created that didn't exist before
    fi
  done
  echo "Done - nothing was changed."
}

echo "Writing new files ..."
write_file "onewire_bus.py" << '__MULSECUBE_EOF__'
"""
Starts and stops 1-Wire buses on chosen GPIO pins while the Pi is running, so a
DS18B20 can be plugged into any port - not just GPIO4.

The Pi's 1-Wire driver listens on one pin, chosen by an overlay. Loading that
overlay for a pin (`dtoverlay w1-gpio gpiopin=<pin>`) creates a new bus there,
and any DS18B20 on it then shows up in /sys/bus/w1/devices like one on GPIO4.
(Overlays loaded at runtime can be removed again; ones written in config.txt can't.)

Usage by the monitor:  begin(pin) -> wait_for_sensors(pin) -> keep it, or release(pin).
"""
import os
import re
import subprocess
import time

from drivers import ds18b20_driver

OVERLAY_NAME = "w1-gpio"        # the Pi 5 lists it as 'w1-gpio-pi5'; the pattern below allows both
WAIT_SECONDS = 20               # how long to wait for a sensor to answer on a new bus

_buses = {}                     # pin -> name of its bus, e.g. 'w1_bus_master2'


class OneWireError(Exception):
    """The bus couldn't be started. The message is safe to show to a person."""


def _run(command):
    return subprocess.run(command, capture_output=True, text=True)


def _error_text(result):
    return result.stderr.strip() or result.stdout.strip() or "no details given"


def _release_gpio_handles():
    """The overlay needs the pin free, so let go of any handle the pin scanner holds."""
    try:
        from gpio_scanner import release_idle_factory
        release_idle_factory()
    except Exception:
        pass


def _masters():
    try:
        return {e for e in os.listdir(ds18b20_driver.W1_BASE_DIR) if e.startswith("w1_bus_master")}
    except OSError:
        return set()


def _slaves(master):
    """DS18B20 ids currently answering on one bus."""
    path = os.path.join(ds18b20_driver.W1_BASE_DIR, master, "w1_master_slaves")
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip().startswith("28-")]
    except OSError:
        return []


def _runtime_overlays():
    """[(index, pin)] for 1-Wire overlays loaded at runtime."""
    try:
        listing = _run(["dtoverlay", "-l"]).stdout
    except FileNotFoundError:
        return []
    found = []
    for line in listing.splitlines():
        match = re.match(rf"\s*(\d+)\s*[:_]\s*{OVERLAY_NAME}\S*(.*)$", line)
        if match:
            pin = re.search(r"gpiopin\s*=\s*(\d+)", match.group(2))
            found.append((int(match.group(1)), int(pin.group(1)) if pin else None))
    return found


def _remove_overlay_for_pin(pin):
    for index, overlay_pin in sorted(_runtime_overlays(), reverse=True):
        if overlay_pin == pin:
            result = _run(["dtoverlay", "-r", str(index)])
            if result.returncode != 0:
                print(f"  WARNING: could not remove the 1-Wire bus on GPIO{pin}: {_error_text(result)}")
                return False
            return True
    return True


# ------------------------------------------------------------------ public --

def owned_pins():
    return set(_buses)


def begin(pin):
    """Starts a bus on `pin`. Quick; call it from the main thread. Raises OneWireError."""
    if pin in _buses:
        return
    _release_gpio_handles()
    before = _masters()
    try:
        result = _run(["dtoverlay", OVERLAY_NAME, f"gpiopin={pin}"])
    except FileNotFoundError:
        raise OneWireError("the 'dtoverlay' tool isn't available on this system")
    if result.returncode != 0:
        raise OneWireError(_error_text(result))

    deadline = time.time() + 3
    while time.time() < deadline:
        new = _masters() - before
        if new:
            _buses[pin] = sorted(new)[0]
            return
        time.sleep(0.1)
    _remove_overlay_for_pin(pin)
    raise OneWireError("the Pi did not create a 1-Wire bus")


def wait_for_sensors(pin, timeout=None, poll=1.0):
    """Waits until a DS18B20 answers on the bus. Slow; call it from a worker thread."""
    master = _buses.get(pin)
    if master is None:
        return []
    end = time.time() + (WAIT_SECONDS if timeout is None else timeout)
    while time.time() < end:
        found = _slaves(master)
        if found:
            return found
        time.sleep(poll)
    return _slaves(master)


def has_sensors(pin):
    master = _buses.get(pin)
    return bool(master and _slaves(master))


def release(pin):
    """Stops the bus on `pin` and hands the pin back."""
    _buses.pop(pin, None)
    _release_gpio_handles()
    return _remove_overlay_for_pin(pin)


def release_all():
    for pin in list(_buses):
        release(pin)


def cleanup_stale():
    """Removes 1-Wire buses an earlier run left loaded. Any runtime one is ours."""
    _buses.clear()
    for _ in range(20):
        overlays = _runtime_overlays()
        if not overlays:
            return
        index = max(i for i, _pin in overlays)
        result = _run(["dtoverlay", "-r", str(index)])
        if result.returncode != 0:
            print(f"  WARNING: could not remove 1-Wire overlay {index}: {_error_text(result)}")
            return
__MULSECUBE_EOF__
write_file "sensor_monitor.py" << '__MULSECUBE_EOF__'
import itertools
import onewire_bus
import queue
import threading
import time

import gpio_scanner
import remote_selector
from drivers import ds18b20_driver, i2c_bus_driver
from profile_registry import (
    find_by_family_code,
    get_i2c_profiles,
    get_manual_registration_profiles,
    get_one_wire_profiles,
)
from sensor_detector import (
    SKIP_OPTION,
    build_1wire_sensor,
    build_gpio_sensor,
    build_i2c_sensor,
    choose_manual_sensor,
    model_label,
    one_wire_help,
    one_wire_label,
)


class SensorMonitor:
    """
    Keeps watching for sensors being plugged in and unplugged, like a PC does
    with USB devices, so nothing needs restarting to change sensors.

    How each kind of sensor is noticed:
      * I2C (e.g. DHT20) and 1-Wire (e.g. DS18B20) identify themselves, so they
        are added automatically and removed when they stop answering.
      * GPIO sensors (DHT11/DHT22, ...) can't identify themselves. A pin that
        reads HIGH is "something is plugged in here", and the dashboard asks
        which sensor it is. Such a sensor counts as unplugged after several
        reads in a row fail.
      * An MQ-8 can't be noticed being unplugged by software at all - its signal
        pin may idle either HIGH or LOW. That needs the planned presence-wire
        hardware. (Add it with the "Add sensor manually" button.)
    """

    CYCLE_SECONDS = 2               # how often sensors are read
    GPIO_SCAN_INTERVAL = 4          # how often the GPIO pins are checked
    UNPLUG_SCANS = 2                # LOW scans in a row = a waiting/skipped pin was unplugged
    SELF_ID_MISSES = 2              # missed checks in a row = an I2C/1-Wire sensor is gone
    READ_FAILURES_BEFORE_REMOVE = 5 # failed reads in a row = a GPIO sensor is gone
    BUS_EMPTY_CHECKS = 3            # cycles with no sensor left on a 1-Wire bus before it is removed
    READ_TIMEOUT = 5                # seconds a single sensor read may take before we move on

    def __init__(self, publisher):
        self.publisher = publisher
        self.sensors = {}           # key -> Sensor.  key = ("1wire", addr) | ("i2c", addr) | ("gpio", pin)
        self.misses = {}            # I2C / 1-Wire key -> consecutive checks where it didn't answer
        self.read_failures = {}     # GPIO key -> consecutive failed reads (kept separate on purpose:
                                    # a good read must never hide a sensor that stopped answering scans)
        self.pending = {}           # pin -> {"cancel": Event}   (a question is open for this pin)
        self.skipped = set()        # pins the user chose "Skip" for while plugged in
        self.ever_read = set()      # sensor keys that have produced at least one good reading
        self.notices = {}           # pin -> cancel Event for an open "check the wiring" message
        self._readers = {}          # key -> helper thread currently reading that sensor
        self.probing = set()                    # pins where a 1-Wire bus is being tried
        self.explicit_probes = set()            # ...because the person chose DS18B20 themselves
        self.probe_results = queue.Queue()      # (pin, sensor ids found) from the waiting threads
        self.bus_empty_counts = {}              # pin -> cycles a 1-Wire bus has had no sensor
        self._notice_ids = itertools.count(1)   # unique ids for messages that aren't tied to a pin
        self.low_counts = {}        # pin -> consecutive LOW scans (only tracked for pending/skipped pins)
        self.answers = queue.Queue()        # (pin, token, choice) from prompt threads
        self.manual_results = queue.Queue() # (profile, pin) from the manual-add thread
        self.manual_thread = None
        self._last_gpio_scan = 0.0

    # ------------------------------------------------------------ helpers --

    def _describe(self, key):
        sensor = self.sensors.get(key)
        model = sensor.profile.model if sensor and sensor.profile else "sensor"
        kind, where = key
        if kind == "manual":
            return f"{model} (analog reading only)"
        if kind == "gpio":
            return f"{model} on GPIO{where}"
        if kind == "i2c":
            return f"{model} on I2C {hex(where)}"
        return f"{model} on 1-Wire {where}"

    def _sensor_id(self, key):
        """A stable name for one physical sensor, so recordings can tell two of the same model apart."""
        kind, where = key
        if kind == "gpio":
            return f"GPIO{where}"
        if kind == "i2c":
            return f"I2C {hex(where)}"
        return str(where)            # 1-Wire serial number, or the model name for a manual sensor

    def _owned_pins(self):
        return ({where for (kind, where) in self.sensors if kind == "gpio"}
                | onewire_bus.owned_pins() | self.probing)

    def _free_pins(self):
        owned = self._owned_pins()
        return [p for p in gpio_scanner.CANDIDATE_PINS if p not in owned]

    def _add(self, key, sensor):
        self.sensors[key] = sensor
        self.misses.pop(key, None)
        self.read_failures.pop(key, None)
        self.ever_read.discard(key)
        print(f"+ Sensor connected: {self._describe(key)}")

    def _remove(self, key, reason, notify=True):
        description = self._describe(key)
        sensor = self.sensors.pop(key, None)
        self.misses.pop(key, None)
        self.read_failures.pop(key, None)
        self.ever_read.discard(key)
        self._readers.pop(key, None)
        if sensor is None:
            return

        never_responded = (reason == "never responded")

        for pin in sensor.gpio_ports:
            try:
                sensor.profile.release_fn(pin)
            except Exception as e:
                print(f"  WARNING: could not release GPIO{pin}: {e}")

        # Forget skip/low memory so the pin is treated as brand new next time
        for pin in sensor.gpio_ports:
            self.skipped.discard(pin)
            self.low_counts.pop(pin, None)

        print(f"- Sensor disconnected: {description} ({reason})")

        if never_responded:
            # It was never working, so there are no panels to clear - and asking
            # "which sensor is it?" again would just repeat the same failure.
            # Keep the pin skipped until it is physically unplugged.
            for pin in sensor.gpio_ports:
                self.skipped.add(pin)
                self._open_notice(pin, sensor.profile.model)
        elif notify:
            remote_selector.notify_sensor_removed(sensor.profile.model)

    # -------------------------------------------- I2C and 1-Wire (automatic) --

    def _sync_self_identifying(self):
        present = {}

        for address in ds18b20_driver.scan_for_ds18b20():
            profile = find_by_family_code(address.split("-")[0])
            if profile:
                present[("1wire", address)] = (profile, address)

        i2c_profiles = get_i2c_profiles()
        if i2c_profiles:
            for address in i2c_bus_driver.probe(list(i2c_profiles)):
                present[("i2c", address)] = (i2c_profiles[address], address)

        for key, (profile, address) in present.items():
            self.misses.pop(key, None)
            if key not in self.sensors:
                kind = key[0]
                sensor = (build_1wire_sensor(profile, address) if kind == "1wire"
                          else build_i2c_sensor(profile, address))
                self._add(key, sensor)

        for key in [k for k in self.sensors if k[0] in ("1wire", "i2c")]:
            if key not in present:
                self.misses[key] = self.misses.get(key, 0) + 1
                if self.misses[key] >= self.SELF_ID_MISSES:
                    self._remove(key, "no longer detected")

    # ------------------------------------------------ GPIO pins (ask the user) --

    def _scan_gpio(self):
        readings = gpio_scanner.scan_pins(self._free_pins())

        for pin, high in readings.items():
            if high:
                self.low_counts.pop(pin, None)
                if pin not in self.pending and pin not in self.skipped:
                    self._start_probe(pin)
            elif pin in self.pending or pin in self.skipped:
                count = self.low_counts.get(pin, 0) + 1
                self.low_counts[pin] = count
                if count >= self.UNPLUG_SCANS:
                    self._forget_pin(pin)

    def _start_probe(self, pin, explicit=False):
        """
        Something is on this pin. First see whether it is a DS18B20 - they identify
        themselves on a 1-Wire bus, so no question is needed. If nothing answers,
        ask the person which sensor it is.
        """
        try:
            onewire_bus.begin(pin)
        except onewire_bus.OneWireError as e:
            print(f"  GPIO{pin}: could not try 1-Wire ({e}).")
            self._after_failed_probe(pin, explicit)
            return

        self.probing.add(pin)
        if explicit:
            self.explicit_probes.add(pin)
        print(f"  GPIO{pin}: checking whether a DS18B20 is connected ...")

        def wait():
            self.probe_results.put((pin, onewire_bus.wait_for_sensors(pin)))

        threading.Thread(target=wait, daemon=True).start()

    def _after_failed_probe(self, pin, explicit):
        if explicit:        # they said it was a DS18B20, and nothing answered: help them find out why
            self.skipped.add(pin)
            model = (get_one_wire_profiles() or [None])[0]
            self._show_notice(pin, f"notice-gpio-{pin}", one_wire_help(model.model if model else "DS18B20", pin))
        else:
            self._open_prompt(pin)

    def _process_probe_results(self):
        while True:
            try:
                pin, found = self.probe_results.get_nowait()
            except queue.Empty:
                return
            self.probing.discard(pin)
            explicit = pin in self.explicit_probes
            self.explicit_probes.discard(pin)
            if found:
                print(f"  GPIO{pin}: found {', '.join(found)} - the 1-Wire bus stays.")
                self.bus_empty_counts[pin] = 0
            else:
                onewire_bus.release(pin)
                self._after_failed_probe(pin, explicit)

    def _release_empty_buses(self):
        """A 1-Wire bus whose sensor was unplugged is removed, which frees the pin for the next sensor."""
        for pin in list(onewire_bus.owned_pins()):
            if pin in self.probing:
                continue
            if onewire_bus.has_sensors(pin):
                self.bus_empty_counts[pin] = 0
                continue
            count = self.bus_empty_counts.get(pin, 0) + 1
            self.bus_empty_counts[pin] = count
            if count >= self.BUS_EMPTY_CHECKS:
                print(f"  GPIO{pin}: the DS18B20 was unplugged - 1-Wire bus removed.")
                onewire_bus.release(pin)
                self.bus_empty_counts.pop(pin, None)

    def _forget_pin(self, pin):
        """The pin went quiet: withdraw any open question and reset what we remember about it."""
        entry = self.pending.pop(pin, None)
        if entry is not None:
            entry["cancel"].set()
            print(f"  GPIO{pin} was unplugged before it was identified.")
        notice = self.notices.pop(pin, None)
        if notice is not None:
            notice.set()
        self.skipped.discard(pin)
        self.low_counts.pop(pin, None)

    def _open_prompt(self, pin):
        cancel = threading.Event()
        self.pending[pin] = {"cancel": cancel}

        profiles = get_manual_registration_profiles()
        options = ([model_label(p) for p in profiles]
                   + [one_wire_label(p) for p in get_one_wire_profiles()]
                   + [SKIP_OPTION])
        prompt = f"Something was plugged into GPIO{pin}. Which sensor is it?"
        print(f"? Something detected on GPIO{pin}")

        def ask():
            choice = remote_selector.request_selection(
                request_id=f"gpio-port-{pin}",
                prompt=prompt,
                options=options,
                cancel_event=cancel,
            )
            self.answers.put((pin, cancel, choice))

        threading.Thread(target=ask, daemon=True).start()

    def _show_notice(self, slot, request_id, text):
        """Shows a message on the dashboard (a popup with one OK button)."""
        previous = self.notices.pop(slot, None)
        if previous is not None:
            previous.set()                  # replace an older message in the same place
        cancel = threading.Event()
        self.notices[slot] = cancel

        def tell():
            remote_selector.request_selection(
                request_id=request_id,
                prompt=text,
                options=["OK"],
                cancel_event=cancel,
            )

        threading.Thread(target=tell, daemon=True).start()

    def _open_notice(self, pin, model):
        """The 'never gave a reading' message for a sensor on a GPIO pin."""
        text = (f"The {model} on GPIO{pin} never gave a reading. Check the wiring "
                f"(data wire on GPIO{pin}, power and ground) and that it is the right "
                f"sensor, then unplug it and plug it back in to try again.")
        self._show_notice(pin, f"notice-gpio-{pin}", text)

    def _process_answers(self):
        while True:
            try:
                pin, token, choice = self.answers.get_nowait()
            except queue.Empty:
                return

            entry = self.pending.get(pin)
            if entry is None or entry["cancel"] is not token:
                continue            # stale answer for a prompt that no longer applies
            del self.pending[pin]

            if choice is None:
                continue            # cancelled, or the dashboard was unreachable
            if choice == SKIP_OPTION:
                self.skipped.add(pin)
                continue

            one_wire = next((p for p in get_one_wire_profiles() if one_wire_label(p) == choice), None)
            if one_wire is not None:
                self._start_probe(pin, explicit=True)       # they say it's a DS18B20: try again
                continue

            profile = next((p for p in get_manual_registration_profiles()
                            if model_label(p) == choice), None)
            if profile is None:
                continue
            self._connect_gpio_sensor(profile, pin)

    def _connect_gpio_sensor(self, profile, pin):
        sensor = build_gpio_sensor(profile, pin)
        if sensor is None:
            # Remember it so we don't ask again every scan; unplug and replug to retry
            self.skipped.add(pin)
            print(f"  Could not set up {profile.model} on GPIO{pin} - "
                  f"unplug and replug it to try again.")
            return
        self._add(("gpio", pin), sensor)

    # ------------------------------------------- manual add (dashboard button) --

    def _check_manual_request(self):
        if self.manual_thread is not None and self.manual_thread.is_alive():
            return
        if not remote_selector.poll_manual_add_request():
            return

        free_pins = self._free_pins()

        def ask():
            self.manual_results.put(choose_manual_sensor(free_pins))

        self.manual_thread = threading.Thread(target=ask, daemon=True)
        self.manual_thread.start()

    def _process_manual_results(self):
        while True:
            try:
                result = self.manual_results.get_nowait()
            except queue.Empty:
                return
            if result is None:
                continue

            profile, pin = result
            if profile.identifier_type == "family_code":    # a DS18B20: try a 1-Wire bus on that pin
                if pin in self._owned_pins() or pin in self.pending:
                    print(f"  GPIO{pin} is already in use - manual add ignored.")
                else:
                    self._start_probe(pin, explicit=True)
                continue
            if pin is None:
                key = ("manual", profile.model)
                if key in self.sensors:
                    print(f"  {profile.model} is already added - manual add ignored.")
                    continue
                sensor = build_gpio_sensor(profile, None)
                if sensor is not None:
                    self._add(key, sensor)
                continue
            if ("gpio", pin) in self.sensors or pin in self.pending:
                print(f"  GPIO{pin} is already in use - manual add ignored.")
                continue
            self.skipped.discard(pin)
            self._connect_gpio_sensor(profile, pin)

    # ------------------------------------------------------ reading sensors --

    def _read_isolated(self, key, sensor):
        """
        Reads one sensor in a helper thread and waits at most READ_TIMEOUT seconds,
        so a sensor that hangs can never freeze the reading of all the others.
        Returns (readings, problem). A read that is still stuck from an earlier
        cycle is not started again - it just counts as not available.
        """
        previous = self._readers.get(key)
        if previous is not None and previous.is_alive():
            return None, "still waiting on an earlier read"

        box = {}

        def work():
            try:
                box["readings"] = sensor.read_value()
            except Exception as e:
                box["error"] = e

        thread = threading.Thread(target=work, daemon=True)
        self._readers[key] = thread
        thread.start()
        thread.join(self.READ_TIMEOUT)

        if thread.is_alive():
            return None, f"read timed out after {self.READ_TIMEOUT}s"
        if "error" in box:
            return None, f"read error: {box['error']}"
        return box.get("readings"), None

    def _read_and_publish(self):
        for key, sensor in list(self.sensors.items()):
            readings, problem = self._read_isolated(key, sensor)

            if not readings:
                print(f"  {sensor.profile.model}: values not available"
                      + (f" ({problem})." if problem else "."))
                if key[0] == "gpio":
                    self.read_failures[key] = self.read_failures.get(key, 0) + 1
                    if self.read_failures[key] >= self.READ_FAILURES_BEFORE_REMOVE:
                        self._remove(key, "stopped responding" if key in self.ever_read
                                     else "never responded")
                continue

            self.read_failures.pop(key, None)
            self.ever_read.add(key)
            for measurement_type, value in readings.items():
                measurement = sensor.profile.get_measurement(measurement_type)
                unit = measurement.unit if measurement else ""
                print(f"  {sensor.profile.model} {measurement_type}: {value}{unit}")

                self.publisher.publish({
                    "model": sensor.profile.model,
                    "sensor_id": self._sensor_id(key),
                    "type": measurement_type,
                    "unit": unit,
                    "value": value,
                    "timestamp": time.time(),
                    "min": measurement.min_value if measurement else None,
                    "max": measurement.max_value if measurement else None,
                })

    # ------------------------------------------------------------- main loop --

    def step(self):
        """One pass: look for changes, then read everything that is connected."""
        self._sync_self_identifying()
        self._process_answers()
        self._process_manual_results()
        self._process_probe_results()

        now = time.time()
        if now - self._last_gpio_scan >= self.GPIO_SCAN_INTERVAL:
            self._scan_gpio()
            self._last_gpio_scan = time.time()

        self._release_empty_buses()
        self._check_manual_request()
        self._read_and_publish()

    def run_forever(self):
        print("Watching for sensors - plug and unplug freely. Press Ctrl+C to stop.\n")
        while True:
            started = time.time()
            self.step()
            print()
            time.sleep(max(0.2, self.CYCLE_SECONDS - (time.time() - started)))

    def shutdown(self):
        """Hand every pin back so the next start begins from a clean slate."""
        for entry in self.pending.values():
            entry["cancel"].set()
        for notice in self.notices.values():
            notice.set()
        for key in list(self.sensors):
            self._remove(key, "shutting down", notify=False)
        onewire_bus.release_all()
        self.probing.clear()
__MULSECUBE_EOF__
write_file "sensor_detector.py" << '__MULSECUBE_EOF__'
"""
Small building blocks used by the sensor monitor: turning "something was found"
into a Sensor object, and the dashboard questions for manually-added sensors.
"""
from sensor import Sensor
from profile_registry import get_manual_registration_profiles, get_one_wire_profiles
import remote_selector

SKIP_OPTION = "Skip this port"
CANCEL_OPTION = "Cancel"
NO_PIN_OPTION = "No DO wire - use the analog reading only"


def model_label(profile):
    return f"{profile.model} ({profile.protocol})"


def one_wire_label(profile):
    """How a 1-Wire sensor appears in the lists."""
    return f"{profile.model} (1-Wire)"


def one_wire_help(model, pin):
    """What to tell someone when a sensor they said was a 1-Wire one didn't answer."""
    return (f"No {model} answered on GPIO{pin}. Check that its DATA wire is on GPIO{pin}, that a "
            f"4.7 kOhm resistor joins DATA to 3.3 V, and that power and ground are right "
            f"(flat side towards you, legs down: GND, DATA, 3.3 V). Then unplug it and plug it "
            f"back in to try again.")


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

    profile = next((p for p in profiles if model_label(p) == choice), None) \
        or next((p for p in one_wire if one_wire_label(p) == choice), None)
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
__MULSECUBE_EOF__
write_file "main.py" << '__MULSECUBE_EOF__'
import signal
import sys

from data_publisher import DataPublisher
from remote_selector import wait_for_hmi
from sensor_monitor import SensorMonitor
from drivers import dht_driver
import onewire_bus

WEBAPP_URL = "http://localhost:5000/api/sensor-data"


def main():
    wait_for_hmi()

    # Start from a clean slate: remove any DHT overlays left over from a
    # previous run, which would otherwise keep the kernel holding those pins.
    dht_driver.cleanup_stale_overlays()
    onewire_bus.cleanup_stale()

    publisher = DataPublisher(WEBAPP_URL)
    monitor = SensorMonitor(publisher)

    # systemd stops a service with SIGTERM, which would normally kill Python
    # instantly. Turn it into a normal exit so the cleanup below always runs.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        monitor.run_forever()
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        monitor.shutdown()
        dht_driver.cleanup_stale_overlays()
        onewire_bus.cleanup_stale()


if __name__ == "__main__":
    main()
__MULSECUBE_EOF__

echo "Checking every file ..."
for f in "${FILES[@]}"; do
  if ! python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read())' "$f" 2>/tmp/mulsecube_check.err; then
    echo "PROBLEM in $f:"
    cat /tmp/mulsecube_check.err
    restore_backup
    exit 1
  fi
done
echo "All files check out."

echo
if systemctl list-unit-files mulsecube-main.service 2>/dev/null | grep -q mulsecube-main; then
  echo "Restarting the sensor service (may ask for your password) ..."
  if [ "$EUID" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
  $SUDO systemctl restart mulsecube-main.service
  echo "Restarted.   Watch it work:   sudo journalctl -u mulsecube-main -f"
else
  echo "The auto-start services aren't installed, so nothing was restarted."
  echo "Run by hand:   python3 hmi_server.py     and in another terminal:   sudo python3 main.py"
fi

echo
echo "Update complete. Your originals are in $BACKUP_DIR/ if you ever need them."
