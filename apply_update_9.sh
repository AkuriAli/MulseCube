#!/bin/bash
# MulseCube update 9: presence pins (port detection).
#   * Each port gets its own presence pin. The plug joins the presence wire to ground, so
#     the program knows exactly when a port is plugged in or unplugged - including the MQ-8.
#   * I2C sensors are matched to their port automatically; a DS18B20 is found on the port's
#     data wire; anything else asks "which sensor is it?" (naming the port).
#   * An MQ-8 reads the ESP32 channel of whichever port it is in.
#   * Your wiring goes in ports_config.py. It is OFF until you set USE_PRESENCE_PINS = True,
#     so nothing changes until your ports are wired. Your edits to it are never overwritten.
#   * Your MQ-8 calibration is kept: that driver is patched in place, not replaced.
#
# Run this from inside your MulseCube folder:   bash apply_update_9.sh

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

for item in main.py sensor_monitor.py hmi_server.py drivers profiles drivers/mq8_esp32_driver.py; do
  if [ ! -e "$item" ]; then
    echo "Can't find '$item' here ($PROJECT_DIR)."
    echo "Put this script inside your MulseCube folder and run it from there."
    exit 1
  fi
done

OWNER="$(stat -c %U "$PROJECT_DIR")"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="backup_before_update9_$STAMP"

FILES=(
  "sensor_monitor.py"
  "ports.py"
  "drivers/digital_output_driver.py"
  "profiles/mq8_profile.py"
)
# files we may touch besides the ones above (backed up if they exist; removed again on rollback if we created them)
EXTRA=("ports_config.py" "drivers/mq8_esp32_driver.py")

echo "Backing up your current files to $BACKUP_DIR/ ..."
for f in "${FILES[@]}" "${EXTRA[@]}"; do
  if [ -f "$f" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$f")"
    cp -p "$f" "$BACKUP_DIR/$f"
  fi
done

fix_owner() { if [ "$EUID" -eq 0 ]; then chown "$OWNER":"$OWNER" "$1"; fi; }

write_file() {
  mkdir -p "$(dirname "$1")"
  cat > "$1"
  fix_owner "$1"
}

restore_backup() {
  echo
  echo "Restoring your original files from $BACKUP_DIR/ ..."
  for f in "${FILES[@]}" "${EXTRA[@]}"; do
    if [ -f "$BACKUP_DIR/$f" ]; then
      cp -p "$BACKUP_DIR/$f" "$f"
    else
      rm -f "$f"          # a file this update created that didn't exist before
    fi
  done
  echo "Done - nothing was changed."
}

echo "Writing new files ..."
write_file "sensor_monitor.py" << '__MULSECUBE_EOF__'
import itertools
import onewire_bus
import ports as port_settings
import queue
import threading
import time

import gpio_scanner
import remote_selector
from drivers import digital_output_driver, ds18b20_driver, i2c_bus_driver
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


I2C_OPTION = "I2C sensor (found automatically)"


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
    PORT_SCAN_INTERVAL = 1.0        # seconds between looks at the presence pins
    PLUG_CONFIRM_READS = 2          # presence readings in a row before a plug / unplug is believed
    I2C_GRACE_SECONDS = 4           # after a plug-in, how long to wait for an I2C sensor to answer
    I2C_MATCH_SECONDS = 8           # an I2C sensor that appeared this close to a plug-in belongs to it
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
        self._added_at = {}                 # sensor key -> when it was connected
        self._last_port_scan = 0.0
        self.use_presence, port_list = port_settings.load()
        self.ports = [dict(p, state="empty", plugged_reads=0, unplugged_reads=0, since=0.0, owner=None)
                      for p in port_list]
        self.port_by_data = {p["data"]: p for p in self.ports}

    # ------------------------------------------------------------ helpers --

    def _describe(self, key):
        sensor = self.sensors.get(key)
        model = sensor.profile.model if sensor and sensor.profile else "sensor"
        kind, where = key
        if kind == "manual":
            return f"{model} (analog reading only)"
        if kind == "gpio":
            port = self.port_by_data.get(where) if self.use_presence else None
            return f"{model} in {port['name']}" if port else f"{model} on GPIO{where}"
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
        self._added_at[key] = time.time()
        print(f"+ Sensor connected: {self._describe(key)}")

    def _remove(self, key, reason, notify=True):
        description = self._describe(key)
        sensor = self.sensors.pop(key, None)
        self.misses.pop(key, None)
        self.read_failures.pop(key, None)
        self.ever_read.discard(key)
        self._readers.pop(key, None)
        self._added_at.pop(key, None)
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
            port = self.port_by_data.get(pin) if self.use_presence else None
            if port is not None and port["state"] == "empty":
                onewire_bus.release(pin)        # it was unplugged while we were checking
                continue
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

    # ------------------------------------------------ ports (presence pins) --

    def _pin_label(self, pin):
        port = self.port_by_data.get(pin) if self.use_presence else None
        return f"{port['name']} (GPIO{pin})" if port else f"GPIO{pin}"

    def _port_owned_keys(self):
        return {p["owner"]["key"] for p in self.ports if p["owner"]}

    def _scan_ports(self):
        """Read each port's presence pin. A plug / unplug is only believed after a few readings in a row."""
        for port in self.ports:
            plugged = digital_output_driver.read_presence(port["presence"])
            if plugged is None:
                continue
            if plugged:
                port["unplugged_reads"] = 0
                port["plugged_reads"] += 1
                if port["state"] == "empty" and port["plugged_reads"] >= self.PLUG_CONFIRM_READS:
                    self._port_plugged(port)
            else:
                port["plugged_reads"] = 0
                port["unplugged_reads"] += 1
                if port["state"] != "empty" and port["unplugged_reads"] >= self.PLUG_CONFIRM_READS:
                    self._port_unplugged(port)
        self._advance_ports()

    def _port_plugged(self, port):
        port["state"] = "waiting"
        port["since"] = time.time()
        print(f"  {port['name']}: something was plugged in.")

    def _advance_ports(self):
        """
        A newly plugged port first waits a few seconds: an I2C sensor announces itself on the
        shared bus, so if exactly one new I2C sensor appeared at the same moment, it is this
        port's. Otherwise the port's data wire is checked for a DS18B20, and failing that the
        person is asked which sensor it is.
        """
        now = time.time()
        waiting = [p for p in self.ports if p["state"] == "waiting"]
        for port in waiting:
            owned = self._port_owned_keys()
            candidates = [k for k in self.sensors if k[0] == "i2c" and k not in owned
                          and abs(self._added_at.get(k, 0) - port["since"]) <= self.I2C_MATCH_SECONDS]
            crowded = [p for p in waiting if p is not port
                       and abs(p["since"] - port["since"]) <= self.I2C_MATCH_SECONDS]
            if len(candidates) == 1 and not crowded:
                port["owner"] = {"kind": "i2c", "key": candidates[0]}
                port["state"] = "occupied"
                print(f"  {port['name']}: {self._describe(candidates[0])}")
            elif now - port["since"] >= self.I2C_GRACE_SECONDS:
                port["state"] = "identifying"
                self._start_probe(port["data"])

    def _port_unplugged(self, port):
        """Everything tied to this port goes: sensors, 1-Wire bus, open questions and messages."""
        data = port["data"]
        print(f"  {port['name']}: unplugged.")
        self._forget_pin(data)
        if data in onewire_bus.owned_pins():
            onewire_bus.release(data)
        key = ("gpio", data)
        if key in self.sensors:
            self._remove(key, "unplugged")
        port["state"] = "empty"
        port["owner"] = None

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
                   + ([I2C_OPTION] if self.use_presence else [])
                   + [SKIP_OPTION])
        label = self._pin_label(pin)
        prompt = f"Something was plugged into {label}. Which sensor is it?"
        print(f"? Something detected on {label}")

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

            if choice == I2C_OPTION:
                print(f"  {self._pin_label(pin)}: noted as an I2C sensor - it appears by itself once it answers.")
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
        port = self.port_by_data.get(pin) if self.use_presence else None
        if port is not None and profile.works_without_pin:
            # an analog sensor in a numbered port: read the ESP32 channel that port's data wire feeds
            sensor.device_address = {"adc_channel": port["adc_channel"]}
            sensor.gpio_ports = []
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
        if self.use_presence:
            if now - self._last_port_scan >= self.PORT_SCAN_INTERVAL:
                self._scan_ports()
                self._last_port_scan = time.time()
        elif now - self._last_gpio_scan >= self.GPIO_SCAN_INTERVAL:
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
        digital_output_driver.release_presence_pins()
__MULSECUBE_EOF__
write_file "ports.py" << '__MULSECUBE_EOF__'
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
__MULSECUBE_EOF__
write_file "drivers/digital_output_driver.py" << '__MULSECUBE_EOF__'
import time

from gpiozero import InputDevice

_pin_devices = {}   # gpio_pin -> InputDevice, kept open across reads for efficiency
_presence_devices = {}   # presence pin -> InputDevice with the Pi's internal pull-up switched on
_reported_errors = set()


def read_raw(gpio_pin):
    """
    Reads a simple HIGH/LOW digital threshold output - e.g. an MQ-series gas
    sensor's DO pin, which just says "above/below threshold", not a real
    protocol. Returns 1 if triggered, 0 if not, or None on error.
    """
    try:
        if gpio_pin not in _pin_devices:
            _pin_devices[gpio_pin] = InputDevice(gpio_pin, pull_up=False)
        return int(_pin_devices[gpio_pin].value)
    except Exception as e:
        print(f"  MQ-8 read error on GPIO{gpio_pin}: {e}")
        return None


def release(gpio_pin):
    """Closes and forgets the handle on this pin (called when the sensor is unplugged)."""
    device = _pin_devices.pop(gpio_pin, None)
    if device is not None:
        try:
            device.close()
        except Exception:
            pass


def read_presence(pin):
    """
    True if something is plugged into the port, False if not, None if the pin can't be
    read reliably. The plug joins the presence wire to ground, so a plugged-in port reads
    LOW, and the Pi's internal pull-up makes an empty port read HIGH.
    """
    try:
        device = _presence_devices.get(pin)
        if device is None:
            device = _presence_devices[pin] = InputDevice(pin, pull_up=True)
            time.sleep(0.05)                    # let the pull-up settle
        samples = []
        for _ in range(3):
            samples.append(bool(device.is_active))      # is_active = the pin is LOW
            time.sleep(0.01)
    except Exception as e:
        message = f"{type(e).__name__}: {e}"
        if (pin, message) not in _reported_errors:
            _reported_errors.add((pin, message))
            print(f"  Presence pin GPIO{pin} problem: {message}")
        return None
    return samples[0] if all(x == samples[0] for x in samples) else None


def release_presence_pins():
    for pin in list(_presence_devices):
        device = _presence_devices.pop(pin)
        try:
            device.close()
        except Exception:
            pass


def has_open_pins():
    """True while any pin is being held open for continuous reading."""
    return bool(_pin_devices) or bool(_presence_devices)
__MULSECUBE_EOF__
write_file "profiles/mq8_profile.py" << '__MULSECUBE_EOF__'
from sensor_profile import SensorProfile
from sensor_measurement import SensorMeasurement
from drivers import digital_output_driver, mq8_esp32_driver


def _read(device_address):
    # device_address is {"adc_channel": n} when the MQ-8 sits in a numbered port (analog only),
    # or the GPIO number of its DO wire, or None if there is no DO wire (default channel).
    if isinstance(device_address, dict):
        alarm, channel = None, device_address.get("adc_channel")
    else:
        alarm = digital_output_driver.read_raw(device_address) if device_address is not None else None
        channel = None
    ppm = mq8_esp32_driver.read_ppm(channel)
    if alarm is None and ppm is None:
        return None
    result = {}
    if ppm is not None:
        result["hydrogen_ppm"] = ppm
    if alarm is not None:
        result["gas_alarm_triggered"] = alarm
    return result


PROFILE = SensorProfile(
    model="MQ-8",
    protocol="Analog (AO via ESP32) + Digital Threshold (DO)",
    is_analog=True,
    identifier_type="manual_gpio",
    identifier=None,
    read_fn=_read,
    release_fn=digital_output_driver.release,
    works_without_pin=True,
    measurements=[
        SensorMeasurement("hydrogen_ppm", "ppm", 100, 10000),
        SensorMeasurement("gas_alarm_triggered", "bool", 0, 1),
    ],
)
__MULSECUBE_EOF__

if [ -f ports_config.py ]; then
  echo "Keeping your existing ports_config.py (your wiring settings are not touched)."
else
  echo "Creating ports_config.py (presence pins are OFF until you switch them on) ..."
  write_file "ports_config.py" << '__MULSECUBE_EOF__'
"""
Your port wiring. Edit this to match your schematic, then change
USE_PRESENCE_PINS to True and restart:   sudo systemctl restart mulsecube-main

Each port has three things:
  presence     the GPIO of the port's presence wire. The plug joins this wire to ground, so a
               plugged-in port reads LOW. The Pi's own built-in pull-up makes an empty port
               read HIGH - no resistor and no extra wire is needed.
  data         the GPIO of the port's data wire (DHT, DS18B20, ...)
  adc_channel  which ESP32 analog input that same data wire also feeds (0, 1 or 2), so an
               analog sensor such as the MQ-8 can be read from any port.

While USE_PRESENCE_PINS is False, the program behaves as before: it looks at the pins
themselves to guess when something is plugged in.
"""

USE_PRESENCE_PINS = False

PORTS = [
    {"name": "Port 1", "presence": 9,  "data": 23, "adc_channel": 0},
    {"name": "Port 2", "presence": 10, "data": 24, "adc_channel": 1},
    {"name": "Port 3", "presence": 11, "data": 25, "adc_channel": 2},
]
__MULSECUBE_EOF__
fi

echo "Updating the MQ-8 driver (your calibration value is kept) ..."
if ! python3 - << 'INNERPY'
import sys
path = "drivers/mq8_esp32_driver.py"
s = open(path).read()
if "def read_ppm(channel=None):" in s:
    print("  already updated.")
    sys.exit(0)
head, sep, tail = s.partition("def read_ppm():")
old = "voltage = esp32_serial_driver.read_channel(MQ8_CHANNEL)"
if not sep or old not in tail:
    print("  PROBLEM: the MQ-8 driver doesn't look the way this update expects.")
    sys.exit(1)
tail = tail.replace(old, "voltage = esp32_serial_driver.read_channel(MQ8_CHANNEL if channel is None else channel)", 1)
open(path, "w").write(head + "def read_ppm(channel=None):" + tail)
print("  updated.")
INNERPY
then
  restore_backup
  exit 1
fi
fix_owner drivers/mq8_esp32_driver.py

echo
echo "Checking every file ..."
for f in "${FILES[@]}" ports_config.py drivers/mq8_esp32_driver.py; do
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
  echo "Restarted."
else
  echo "The auto-start services aren't installed, so nothing was restarted."
fi

echo
echo "Update complete. Your originals are in $BACKUP_DIR/ if you ever need them."
echo "Presence pins are still OFF. When your ports are wired, edit ports_config.py,"
echo "set USE_PRESENCE_PINS = True, and restart:   sudo systemctl restart mulsecube-main"
