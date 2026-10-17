"""
Records what the sensors report into the database.

While recording, a timer takes a snapshot of the latest value from every sensor
every `interval` seconds and stores it. Taking snapshots (instead of storing
every reading as it arrives) means all values in a snapshot share one timestamp,
which gives a clean table and lets you choose how much data a long recording makes.

A sensor with no fresh reading when a snapshot is taken (unplugged, or silent for
longer than `stale_after` seconds) is simply left out of that snapshot, so gaps in
the data show up as gaps rather than as repeated old values.

A recording survives the program restarting: it is stored as "not yet ended", and
is picked up again when the program starts.
"""
import math
import threading
import time
from datetime import datetime

DEFAULT_STALE_AFTER = 10      # seconds - same limit the dashboard uses for "No recent data"
MIN_INTERVAL = 1.0            # seconds


class RecorderError(Exception):
    """Something the caller did wrong (bad interval, already recording...). Safe to show to a user."""


class Recorder:
    def __init__(self, db, stale_after=DEFAULT_STALE_AFTER, min_interval=MIN_INTERVAL):
        self.db = db
        self.stale_after = stale_after
        self.min_interval = min_interval

        self._latest = {}                  # (sensor_id, model, measurement) -> {value, unit, received}
        self._data_lock = threading.Lock()
        self._control_lock = threading.RLock()
        self._thread = None
        self._stop_event = None
        self._session_id = None
        self.last_error = None
        self.resumed = False

        self._resume_if_needed()

    # ------------------------------------------------------------ incoming --

    def ingest(self, reading):
        """Remember the newest value from a sensor. Called for every reading that arrives."""
        try:
            value = float(reading["value"])
        except (KeyError, TypeError, ValueError):
            return
        if math.isnan(value) or math.isinf(value):
            return                          # can't be stored or charted

        model = reading.get("model")
        measurement = reading.get("type")
        if not model or not measurement:
            return

        sensor_id = str(reading.get("sensor_id") or model)
        with self._data_lock:
            self._latest[(sensor_id, model, measurement)] = {
                "value": value,
                "unit": reading.get("unit") or "",
                "received": time.time(),
            }

    def forget_model(self, model):
        """A sensor of this model was unplugged - stop recording its old values straight away."""
        with self._data_lock:
            for key in [k for k in self._latest if k[1] == model]:
                del self._latest[key]

    # ------------------------------------------------------------- control --

    def start(self, interval_s, name=None):
        try:
            interval = float(interval_s)
        except (TypeError, ValueError):
            raise RecorderError("The interval must be a number of seconds.")
        if interval < self.min_interval:
            raise RecorderError(f"The interval can't be shorter than {self.min_interval:g} second(s).")
        if interval > 24 * 3600:
            raise RecorderError("The interval can't be longer than a day.")

        with self._control_lock:
            if self._session_id is not None:
                raise RecorderError("A recording is already running.")
            now = time.time()
            name = (name or "").strip() or "Recording " + datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
            session_id = self.db.create_session(name, now, interval)
            self._begin(session_id, interval)
        return self.status()

    def stop(self):
        with self._control_lock:
            if self._session_id is None:
                return self.status()
            session_id = self._session_id
            self._stop_event.set()
            thread = self._thread
            self._session_id = None
            self._thread = None
        if thread is not None:
            thread.join(timeout=10)
        self.db.end_session(session_id, time.time())
        return self.status()

    def status(self):
        session = self.db.get_session(self._session_id) if self._session_id is not None else None
        now = time.time()
        if session is None:
            return {"recording": False, "error": self.last_error}
        return {
            "recording": True,
            "session_id": session["id"],
            "name": session["name"],
            "started": session["started"],
            "interval": session["interval_s"],
            "elapsed": now - session["started"],
            "samples": session["sample_count"],
            "resumed": self.resumed,
            "error": self.last_error,
        }

    # ------------------------------------------------------------ internals --

    def _resume_if_needed(self):
        """If the program stopped while recording, carry on with the same recording."""
        active = self.db.active_session()
        if active is None:
            return
        self.db.close_other_active_sessions(active["id"])
        self.resumed = True
        with self._control_lock:
            self._begin(active["id"], active["interval_s"])

    def _begin(self, session_id, interval):
        self._stop_event = threading.Event()
        self._session_id = session_id
        self.last_error = None
        self._thread = threading.Thread(
            target=self._run, args=(session_id, interval, self._stop_event),
            name="recorder", daemon=True,
        )
        self._thread.start()

    def _run(self, session_id, interval, stop_event):
        next_tick = time.time()
        while True:
            try:
                self._snapshot(session_id)
                self.last_error = None
            except Exception as e:           # e.g. disk full - keep trying, and say so (once)
                message = f"{type(e).__name__}: {e}"
                if message != self.last_error:
                    print(f"  Recorder: could not save a sample ({message})")
                self.last_error = message

            next_tick += interval
            delay = next_tick - time.time()
            if delay < 0:                    # fell behind (slow disk, clock jump): don't try to catch up
                next_tick = time.time() + interval
                delay = interval
            if stop_event.wait(delay):
                return

    def _snapshot(self, session_id):
        now = time.time()
        with self._data_lock:
            rows = [
                (now, sensor_id, model, measurement, info["unit"], info["value"])
                for (sensor_id, model, measurement), info in self._latest.items()
                if now - info["received"] <= self.stale_after
            ]
        if rows:
            self.db.add_samples(session_id, rows)
