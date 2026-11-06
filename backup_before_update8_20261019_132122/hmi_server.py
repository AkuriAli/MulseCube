import time
from collections import deque
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

# Recording is an extra: if it can't start (e.g. the data folder isn't writable) the
# live dashboard must still work, so any problem here is reported and then ignored.
try:
    from database import Database
    from recorder import Recorder, RecorderError
    recorder = Recorder(Database())
    if recorder.resumed:
        print("Recording was in progress when the server last stopped - carrying on with it.")
except Exception as e:
    print(f"WARNING: recording is not available: {e}")
    recorder = None

    class RecorderError(Exception):
        pass

MAX_HISTORY_POINTS = 120   # ~4 minutes of history at a 2s reporting interval

# In-memory store: { model: { measurement_type: {value, unit, timestamp, min, max} } }
latest_readings = {}

# In-memory store: { "model|type": deque([{value, timestamp}, ...]) }
reading_history = {}

# In-memory store: { request_id: {prompt, options, answered, choice} }
pending_selections = {}

# Set when someone presses 'Add sensor manually'; the sensor loop collects it
manual_add_requested = False

STALE_AFTER_SECONDS = 10


@app.route("/api/sensor-data", methods=["POST"])
def receive_data():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "no data received"}), 400

    model = data.get("model")
    measurement_type = data.get("type")
    if not model or not measurement_type:
        return jsonify({"status": "error", "message": "missing model/type"}), 400

    timestamp = data.get("timestamp", time.time())
    value = data.get("value")

    latest_readings.setdefault(model, {})[measurement_type] = {
        "value": value,
        "unit": data.get("unit", ""),
        "timestamp": timestamp,
        "min": data.get("min"),
        "max": data.get("max"),
    }
    if recorder is not None:
        recorder.ingest(data)

    history_key = f"{model}|{measurement_type}"
    if history_key not in reading_history:
        reading_history[history_key] = deque(maxlen=MAX_HISTORY_POINTS)
    reading_history[history_key].append({"value": value, "timestamp": timestamp})

    return jsonify({"status": "ok"}), 200


@app.route("/api/latest")
def get_latest():
    # Age is computed here, on the Pi, from the Pi's own clock - the same
    # clock that stamped each reading. This keeps "live vs stale" correct
    # no matter what device is viewing the dashboard (a laptop or phone's
    # clock can easily disagree with an offline Pi's clock).
    now = time.time()
    result = {}
    for model, measurements in latest_readings.items():
        result[model] = {}
        for measurement_type, info in measurements.items():
            entry = dict(info)
            entry["age"] = max(0.0, now - info["timestamp"])
            result[model][measurement_type] = entry
    return jsonify(result)


@app.route("/api/history")
def get_history():
    model = request.args.get("model")
    measurement_type = request.args.get("type")
    key = f"{model}|{measurement_type}"
    return jsonify(list(reading_history.get(key, [])))


@app.route("/api/selection-request", methods=["POST"])
def selection_request():
    data = request.get_json()
    request_id = data["request_id"]
    pending_selections[request_id] = {
        "prompt": data["prompt"],
        "options": data["options"],
        "answered": False,
        "choice": None,
    }
    return jsonify({"status": "ok"})


@app.route("/api/selection-answer/<request_id>")
def get_selection_answer(request_id):
    entry = pending_selections.get(request_id)
    if not entry:
        return jsonify({"answered": False})
    return jsonify({"answered": entry["answered"], "choice": entry["choice"]})


@app.route("/api/selection-answer", methods=["POST"])
def post_selection_answer():
    data = request.get_json()
    request_id = data["request_id"]
    entry = pending_selections.get(request_id)
    if entry:
        entry["answered"] = True
        entry["choice"] = data["choice"]
    return jsonify({"status": "ok"})


@app.route("/api/selection-cancel", methods=["POST"])
def selection_cancel():
    """Withdraws a question (e.g. the sensor was unplugged before anyone answered)."""
    data = request.get_json(silent=True) or {}
    pending_selections.pop(data.get("request_id"), None)
    return jsonify({"status": "ok"})


@app.route("/api/sensor-removed", methods=["POST"])
def sensor_removed():
    """A sensor was unplugged: drop its panels and graph history."""
    data = request.get_json(silent=True) or {}
    model = data.get("model")
    latest_readings.pop(model, None)
    for key in [k for k in reading_history if k.startswith(f"{model}|")]:
        del reading_history[key]
    if recorder is not None:
        recorder.forget_model(model)
    return jsonify({"status": "ok"})


@app.route("/api/manual-add", methods=["GET", "POST"])
def manual_add():
    """POST (the dashboard button) raises a flag; GET (the sensor loop) reads and clears it."""
    global manual_add_requested
    if request.method == "POST":
        manual_add_requested = True
        return jsonify({"status": "ok"})
    requested = manual_add_requested
    manual_add_requested = False
    return jsonify({"requested": requested})


@app.route("/api/pending-selections")
def get_pending_selections():
    return jsonify({rid: v for rid, v in pending_selections.items() if not v["answered"]})


# ---- Recording and Excel download ------------------------------------------

def _recording_unavailable():
    return jsonify({"error": "Recording isn't available on this system - see the dashboard server's log."}), 503


@app.route("/api/recording")
def recording_status():
    if recorder is None:
        return _recording_unavailable()
    return jsonify(recorder.status())


@app.route("/api/recording/start", methods=["POST"])
def recording_start():
    if recorder is None:
        return _recording_unavailable()
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(recorder.start(data.get("interval", 10), data.get("name")))
    except RecorderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/recording/stop", methods=["POST"])
def recording_stop():
    if recorder is None:
        return _recording_unavailable()
    return jsonify(recorder.stop())


@app.route("/api/recordings")
def recordings_list():
    if recorder is None:
        return _recording_unavailable()
    active = recorder.status().get("session_id")
    sessions = recorder.db.list_sessions()
    for s in sessions:
        s["recording"] = (s["id"] == active)
    return jsonify(sessions)


@app.route("/api/recordings/<int:session_id>/export.xlsx")
def recording_export(session_id):
    if recorder is None:
        return _recording_unavailable()
    try:
        from exporter import build_workbook
    except ImportError:
        return jsonify({"error": "Excel export needs the 'openpyxl' package. On the Pi run: "
                                 "pip install openpyxl --break-system-packages   "
                                 "then restart the dashboard service."}), 503
    try:
        workbook, filename = build_workbook(recorder.db, session_id)
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    return send_file(
        workbook,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/")
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MulseCube</title>
<style>
  /* Fonts are served from this Pi (static/fonts/), not from the internet.
     font-display: swap means text shows immediately in a fallback font
     and switches over the instant the local file loads. */
  @font-face { font-family: 'Source Serif 4'; font-weight: 500; font-display: swap;
    src: url('/static/fonts/source-serif-4-latin-500-normal.woff2') format('woff2'); }
  @font-face { font-family: 'Source Serif 4'; font-weight: 600; font-display: swap;
    src: url('/static/fonts/source-serif-4-latin-600-normal.woff2') format('woff2'); }
  @font-face { font-family: 'IBM Plex Sans'; font-weight: 400; font-display: swap;
    src: url('/static/fonts/ibm-plex-sans-latin-400-normal.woff2') format('woff2'); }
  @font-face { font-family: 'IBM Plex Sans'; font-weight: 500; font-display: swap;
    src: url('/static/fonts/ibm-plex-sans-latin-500-normal.woff2') format('woff2'); }
  @font-face { font-family: 'IBM Plex Mono'; font-weight: 500; font-display: swap;
    src: url('/static/fonts/ibm-plex-mono-latin-500-normal.woff2') format('woff2'); }
  @font-face { font-family: 'IBM Plex Mono'; font-weight: 600; font-display: swap;
    src: url('/static/fonts/ibm-plex-mono-latin-600-normal.woff2') format('woff2'); }

  :root {
    --bg: #EDEEE9;
    --panel: #FFFFFF;
    --ink: #1B2430;
    --ink-soft: #5B6472;
    --live: #1F6F78;
    --stale: #B23B3B;
    --alert: #A8672B;
    --hairline: #D7D9D2;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
    padding: 2.5rem clamp(1rem, 4vw, 3rem);
  }

  header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    border-bottom: 1px solid var(--hairline);
    padding-bottom: 1.25rem;
    margin-bottom: 2rem;
    flex-wrap: wrap;
    gap: 0.75rem;
  }

  h1 {
    font-family: 'Source Serif 4', Georgia, 'Times New Roman', serif;
    font-weight: 600;
    font-size: 1.75rem;
    margin: 0;
    letter-spacing: 0.01em;
  }

  #summary {
    font-size: 0.9rem;
    color: var(--ink-soft);
  }

  .header-right {
    display: flex;
    align-items: baseline;
    gap: 1rem;
    flex-wrap: wrap;
  }

  #add-sensor {
    font-family: inherit;
    font-size: 0.85rem;
    color: var(--ink);
    background: var(--panel);
    border: 1px solid var(--hairline);
    padding: 0.4rem 0.8rem;
    cursor: pointer;
  }

  #add-sensor:hover { border-color: var(--live); }

  #recorder {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.75rem 2rem;
    background: var(--panel);
    border: 1px solid var(--hairline);
    border-top: 3px solid var(--hairline);
    padding: 0.9rem 1.25rem;
    margin-bottom: 1.5rem;
  }

  #recorder.recording { border-top-color: var(--stale); }

  .rec-group {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
  }

  .rec-field {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    color: var(--ink-soft);
  }

  #recorder select,
  #recorder button {
    font-family: inherit;
    font-size: 0.85rem;
    color: var(--ink);
    background: var(--panel);
    border: 1px solid var(--hairline);
    padding: 0.4rem 0.7rem;
    cursor: pointer;
  }

  #recorder select { max-width: 22rem; }

  #recorder button:hover:not(:disabled),
  #recorder select:hover:not(:disabled) { border-color: var(--live); }

  #recorder button:disabled,
  #recorder select:disabled { opacity: 0.5; cursor: default; }

  #rec-toggle {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    min-width: 10rem;
  }

  #rec-toggle .dot {
    width: 0.65rem;
    height: 0.65rem;
    border-radius: 50%;
    background: var(--stale);
  }

  #recorder.recording #rec-toggle { border-color: var(--stale); }
  #recorder.recording #rec-toggle .dot { border-radius: 0; animation: rec-pulse 1.6s ease-in-out infinite; }

  @keyframes rec-pulse { 50% { opacity: 0.35; } }
  @media (prefers-reduced-motion: reduce) {
    #recorder.recording #rec-toggle .dot { animation: none; }
  }

  #rec-status {
    font-family: 'IBM Plex Mono', ui-monospace, monospace;
    font-size: 0.8rem;
    color: var(--ink-soft);
  }

  #recorder.recording #rec-status { color: var(--stale); }

  #rec-message { font-size: 0.8rem; color: var(--ink-soft); }
  #rec-message.error { color: var(--stale); }

  #grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 1px;
    background: var(--hairline);
    border: 1px solid var(--hairline);
  }

  .panel {
    background: var(--panel);
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
    border-top: 3px solid var(--live);
    cursor: pointer;
  }

  .panel.stale { border-top-color: var(--stale); }
  .panel.alert { border-top-color: var(--alert); }

  .panel .port-label {
    font-size: 0.75rem;
    color: var(--ink-soft);
    letter-spacing: 0.04em;
  }

  .panel .measurement {
    font-size: 0.95rem;
    color: var(--ink);
  }

  .panel .value {
    font-family: 'IBM Plex Mono', ui-monospace, 'Courier New', monospace;
    font-weight: 600;
    font-size: 2.4rem;
    font-variant-numeric: tabular-nums;
    line-height: 1.1;
  }

  .panel .status {
    font-size: 0.78rem;
    color: var(--live);
  }

  .panel.stale .status { color: var(--stale); }
  .panel.alert .status { color: var(--alert); }

  .panel .range-note {
    font-size: 0.72rem;
    color: var(--alert);
  }

  .panel .chart {
    width: 100%;
    height: 280px;
    margin-top: 0.5rem;
    border: 1px solid var(--hairline);
    background: var(--panel);
  }

  .panel.expanded {
    grid-column: 1 / -1;
  }

  .panel .expand-hint {
    font-size: 0.7rem;
    color: var(--ink-soft);
    letter-spacing: 0.03em;
  }

  #empty {
    padding: 3rem 1rem;
    text-align: center;
    color: var(--ink-soft);
    border: 1px dashed var(--hairline);
  }

  #selection-overlay {
    position: fixed;
    inset: 0;
    background: rgba(27, 36, 48, 0.55);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 10;
  }

  #selection-box {
    background: var(--panel);
    border: 1px solid var(--hairline);
    padding: 2rem;
    max-width: 420px;
    width: 90%;
  }

  #selection-prompt {
    font-family: 'Source Serif 4', Georgia, 'Times New Roman', serif;
    font-size: 1.15rem;
    margin-bottom: 1.25rem;
  }

  .selection-option {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.75rem 1rem;
    margin-bottom: 0.5rem;
    background: var(--bg);
    border: 1px solid var(--hairline);
    color: var(--ink);
    font-family: 'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
    font-size: 0.95rem;
    cursor: pointer;
  }

  .selection-option:hover {
    border-color: var(--live);
  }
</style>
</head>
<body>

<header>
  <h1>MulseCube</h1>
  <div class="header-right">
    <span id="summary">Waiting for readings&hellip;</span>
    <button id="add-sensor" type="button">+ Add sensor manually</button>
  </div>
</header>

<section id="recorder">
  <div class="rec-group">
    <button id="rec-toggle" type="button"><span class="dot"></span><span id="rec-toggle-label">Record</span></button>
    <label class="rec-field">Save a reading every
      <select id="rec-interval">
        <option value="2">2 seconds</option>
        <option value="5">5 seconds</option>
        <option value="10" selected>10 seconds</option>
        <option value="30">30 seconds</option>
        <option value="60">1 minute</option>
        <option value="300">5 minutes</option>
      </select>
    </label>
    <span id="rec-status">Not recording</span>
  </div>
  <div class="rec-group">
    <label class="rec-field">Recording
      <select id="rec-session"><option value="">None yet</option></select>
    </label>
    <button id="rec-download" type="button" disabled>Download Excel</button>
    <span id="rec-message"></span>
  </div>
</section>

<div id="grid"></div>
<div id="empty" style="display:none;">No sensors reporting yet. Connect a sensor and confirm it on the dashboard to see live values here.</div>

<div id="selection-overlay">
  <div id="selection-box">
    <div id="selection-prompt"></div>
    <div id="selection-options"></div>
  </div>
</div>

<script>
  const STALE_AFTER_SECONDS = 10;

  // Measurement types whose graph zooms in on the actual readings instead of
  // stretching to the sensor's full rated range (which would flatten them).
  const ZOOMED_TYPES = ['temperature'];
  // Smallest temperature span the zoomed graph will show, in degrees. Smaller =
  // more zoomed in. With a ~230px tall plot, 4 degrees gives ~40px per degree.
  const ZOOMED_MIN_SPAN = 4;
  const expandedPanels = new Set();

  function niceNumber(value, round) {
    if (value === 0) return 0;
    const exponent = Math.floor(Math.log10(Math.abs(value)));
    const fraction = Math.abs(value) / Math.pow(10, exponent);
    let niceFraction;
    if (round) {
      if (fraction < 1.5) niceFraction = 1;
      else if (fraction < 3) niceFraction = 2;
      else if (fraction < 7) niceFraction = 5;
      else niceFraction = 10;
    } else {
      if (fraction <= 1) niceFraction = 1;
      else if (fraction <= 2) niceFraction = 2;
      else if (fraction <= 5) niceFraction = 5;
      else niceFraction = 10;
    }
    return niceFraction * Math.pow(10, exponent);
  }

  function niceTicks(min, max, tickCount) {
    const range = niceNumber(max - min, false) || 1;
    const step = niceNumber(range / (tickCount - 1), true) || 1;
    const niceMin = Math.floor(min / step) * step;
    const niceMax = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = niceMin; v <= niceMax + step * 0.5; v += step) {
      ticks.push(Math.round(v * 10000) / 10000);
    }
    return ticks;
  }

  // Like niceTicks, but never widens the axis: it only picks round-numbered
  // gridlines that fall inside [min, max]. Used for the zoomed graphs.
  function ticksWithin(min, max, tickCount) {
    const step = niceNumber((max - min) / (tickCount - 1), true) || 1;
    const first = Math.ceil(min / step);
    const last = Math.floor(max / step);
    const ticks = [];
    for (let i = first; i <= last; i++) {
      ticks.push(Math.round(i * step * 10000) / 10000);
    }
    return ticks.length >= 2 ? ticks : [min, max];
  }

  function formatSecondsAgo(seconds) {
    if (seconds <= 0) return 'now';
    if (seconds < 60) return `-${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return secs ? `-${mins}m${secs}s` : `-${mins}m`;
  }

  function drawLineChart(canvas, points, minRange, maxRange, unit, type) {
    const ctx = canvas.getContext('2d');
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const padding = { top: 16, right: 24, bottom: 32, left: 56 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;

    if (!points || points.length < 2) {
      ctx.fillStyle = '#5B6472';
      ctx.font = '12px "IBM Plex Sans", system-ui, sans-serif';
      ctx.fillText('Not enough data yet', padding.left, height / 2);
      return;
    }

    const values = points.map(p => p.value);
    const hasRange = (minRange !== null && minRange !== undefined && maxRange !== null && maxRange !== undefined);

    const dataMin = Math.min(...values);
    const dataMax = Math.max(...values);
    const zoomed = ZOOMED_TYPES.includes(type);

    let axisMin, axisMax, yTicks, tickMin, tickMax;
    if (zoomed) {
      // Fit the axis to the readings themselves (never narrower than
      // ZOOMED_MIN_SPAN), so a one-degree change is clearly visible.
      const center = (dataMin + dataMax) / 2;
      const span = Math.max(dataMax - dataMin, ZOOMED_MIN_SPAN);
      axisMin = center - span / 2 - span * 0.15;
      axisMax = center + span / 2 + span * 0.15;
      yTicks = ticksWithin(axisMin, axisMax, 5);
      tickMin = axisMin;
      tickMax = axisMax;
    } else {
      // Everything else (e.g. humidity) keeps the full rated range in view.
      axisMin = hasRange ? Math.min(dataMin, minRange) : dataMin;
      axisMax = hasRange ? Math.max(dataMax, maxRange) : dataMax;
      const spread = (axisMax - axisMin) || 1;
      axisMin -= spread * 0.12;
      axisMax += spread * 0.12;
      yTicks = niceTicks(axisMin, axisMax, 5);
      tickMin = yTicks[0];
      tickMax = yTicks[yTicks.length - 1];
    }

    function yFor(v) {
      return padding.top + plotHeight - ((v - tickMin) / (tickMax - tickMin)) * plotHeight;
    }

    // This is what makes the graph "zoom out" over time - the visible time
    // span is however much real history exists so far (up to ~4 minutes),
    // not a fixed window, so it fills in and widens as more data arrives.
    const newestTime = points[points.length - 1].timestamp;
    const oldestTime = points[0].timestamp;
    const timeSpan = Math.max(newestTime - oldestTime, 1);

    function xFor(t) {
      return padding.left + ((t - oldestTime) / timeSpan) * plotWidth;
    }

    // Y-axis gridlines and numeric labels
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.font = '11px "IBM Plex Mono", ui-monospace, monospace';
    yTicks.forEach(tick => {
      const y = yFor(tick);
      ctx.strokeStyle = '#E7E8E2';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillStyle = '#5B6472';
      ctx.fillText(String(tick), padding.left - 8, y);
    });

    // X-axis time labels, relative to the most recent reading
    const xTickCount = 4;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let i = 0; i <= xTickCount; i++) {
      const t = oldestTime + (timeSpan * i / xTickCount);
      const x = xFor(t);
      ctx.fillStyle = '#5B6472';
      ctx.fillText(formatSecondsAgo(newestTime - t), x, padding.top + plotHeight + 8);
    }

    // Axis titles
    ctx.save();
    ctx.translate(14, padding.top + plotHeight / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#1B2430';
    ctx.font = '10px "IBM Plex Sans", system-ui, sans-serif';
    ctx.fillText(unit || 'value', 0, 0);
    ctx.restore();

    ctx.textAlign = 'center';
    ctx.fillStyle = '#1B2430';
    ctx.font = '10px "IBM Plex Sans", system-ui, sans-serif';
    ctx.fillText('time', padding.left + plotWidth / 2, height - 8);

    // Expected min/max drawn as explicit labeled limit lines, not just a shaded band
    if (hasRange) {
      [{ v: minRange, label: 'min' }, { v: maxRange, label: 'max' }].forEach(({ v, label }) => {
        if (v < tickMin || v > tickMax) {
          // Zoomed in past this limit: keep it on the graph as an edge note
          const above = v > tickMax;
          ctx.fillStyle = '#A8672B';
          ctx.font = '10px \\"IBM Plex Mono\\", ui-monospace, monospace';
          ctx.textAlign = 'right';
          ctx.textBaseline = above ? 'top' : 'bottom';
          ctx.fillText(`${above ? '\\u25b2' : '\\u25bc'} ${label} ${v} (off scale)`,
                       width - padding.right - 4,
                       above ? padding.top + 4 : padding.top + plotHeight - 4);
          return;
        }

        const y = yFor(v);
        ctx.strokeStyle = '#A8672B';
        ctx.setLineDash([5, 4]);
        ctx.lineWidth = 1.2;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = '#A8672B';
        ctx.font = '10px "IBM Plex Mono", ui-monospace, monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = label === 'min' ? 'top' : 'bottom';
        ctx.fillText(`${label} ${v}`, width - padding.right - 66, y + (label === 'min' ? 3 : -3));
      });
    }

    // The actual data line
    ctx.strokeStyle = '#1F6F78';
    ctx.lineWidth = 2;
    ctx.beginPath();
    points.forEach((p, i) => {
      const x = xFor(p.timestamp);
      const y = yFor(p.value);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();

    const lastX = xFor(newestTime);
    const lastY = yFor(points[points.length - 1].value);
    ctx.fillStyle = '#1F6F78';
    ctx.beginPath();
    ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
    ctx.fill();

    // Axis lines
    ctx.strokeStyle = '#5B6472';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, padding.top + plotHeight);
    ctx.lineTo(padding.left + plotWidth, padding.top + plotHeight);
    ctx.stroke();
  }

  async function loadHistoryAndDraw(model, type, canvas, minRange, maxRange, unit) {
    try {
      const res = await fetch(`/api/history?model=${encodeURIComponent(model)}&type=${encodeURIComponent(type)}`);
      const points = await res.json();
      drawLineChart(canvas, points, minRange, maxRange, unit, type);
    } catch (e) {
      // ignore transient errors
    }
  }

  async function checkPendingSelections() {
    let pending;
    try {
      const res = await fetch('/api/pending-selections');
      pending = await res.json();
    } catch (e) {
      return;
    }

    const overlay = document.getElementById('selection-overlay');
    const ids = Object.keys(pending);

    if (ids.length === 0) {
      overlay.style.display = 'none';
      return;
    }

    const requestId = ids[0];
    const entry = pending[requestId];

    document.getElementById('selection-prompt').textContent = entry.prompt;
    const optionsDiv = document.getElementById('selection-options');
    optionsDiv.innerHTML = '';

    entry.options.forEach(option => {
      const btn = document.createElement('button');
      btn.className = 'selection-option';
      btn.textContent = option;
      btn.onclick = () => answerSelection(requestId, option);
      optionsDiv.appendChild(btn);
    });

    overlay.style.display = 'flex';
  }

  async function answerSelection(requestId, choice) {
    await fetch('/api/selection-answer', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({request_id: requestId, choice: choice})
    });
    document.getElementById('selection-overlay').style.display = 'none';
  }

  async function refresh() {
    let data;
    try {
      const res = await fetch('/api/latest');
      data = await res.json();
    } catch (e) {
      return;
    }

    const grid = document.getElementById('grid');
    const empty = document.getElementById('empty');
    const summary = document.getElementById('summary');
    grid.innerHTML = '';

    let panelCount = 0;
    let liveCount = 0;

    for (const [model, measurements] of Object.entries(data)) {
      for (const [type, info] of Object.entries(measurements)) {
        panelCount++;
        const key = `${model}|${type}`;
        const age = info.age;   // computed by the Pi, not the browser's clock
        const stale = age > STALE_AFTER_SECONDS;
        const isBoolean = info.unit === 'bool';
        const detected = isBoolean && Number(info.value) === 1;
        const hasRange = (info.min !== null && info.min !== undefined && info.max !== null && info.max !== undefined);
        const outOfRange = !isBoolean && hasRange && (Number(info.value) < info.min || Number(info.value) > info.max);
        if (!stale) liveCount++;

        let panelClass = 'panel';
        if (stale) panelClass += ' stale';
        else if (detected || outOfRange) panelClass += ' alert';

        const isExpanded = expandedPanels.has(key);
        if (isExpanded) panelClass += ' expanded';

        const displayValue = isBoolean
          ? (detected ? 'Detected' : 'Not detected')
          : `${info.value}${info.unit}`;

        const panel = document.createElement('div');
        panel.className = panelClass;
        panel.innerHTML = `
          <div class="port-label">${model}</div>
          <div class="measurement">${type.replace(/_/g, ' ')}</div>
          <div class="value">${displayValue}</div>
          ${outOfRange ? `<div class="range-note">Outside expected range (${info.min}\\u2013${info.max}${info.unit})</div>` : ''}
          <div class="status">${stale ? 'No recent data' : 'Live \\u00b7 updated ' + Math.round(age) + 's ago'}</div>
          <div class="expand-hint">${isExpanded ? '\\u25b2 tap to hide graph' : '\\u25bc tap for graph'}</div>
          ${isExpanded ? '<canvas class="chart"></canvas>' : ''}
        `;

        panel.addEventListener('click', () => {
          if (expandedPanels.has(key)) expandedPanels.delete(key);
          else expandedPanels.add(key);
          refresh();
        });

        grid.appendChild(panel);

        if (isExpanded) {
          const canvas = panel.querySelector('canvas.chart');
          loadHistoryAndDraw(model, type, canvas, info.min, info.max, info.unit);
        }
      }
    }

    empty.style.display = panelCount === 0 ? 'block' : 'none';
    summary.textContent = panelCount === 0
      ? 'Waiting for readings\\u2026'
      : `${liveCount} of ${panelCount} reading${panelCount === 1 ? '' : 's'} live`;
  }

  document.getElementById('add-sensor').addEventListener('click', async () => {
    try {
      await fetch('/api/manual-add', {method: 'POST'});
    } catch (e) { /* ignore */ }
  });

  // ---- Recording and Excel download ----
  const rec = {recording: false, sessionId: null, userPicked: false, sessionIds: 'none', busy: false};
  const recEl = {
    box: document.getElementById('recorder'),
    toggle: document.getElementById('rec-toggle'),
    toggleLabel: document.getElementById('rec-toggle-label'),
    interval: document.getElementById('rec-interval'),
    status: document.getElementById('rec-status'),
    session: document.getElementById('rec-session'),
    download: document.getElementById('rec-download'),
    message: document.getElementById('rec-message'),
  };

  function recSay(text, isError) {
    recEl.message.textContent = text || '';
    recEl.message.className = isError ? 'error' : '';
  }

  function formatDuration(seconds) {
    seconds = Math.max(0, Math.round(seconds));
    const pad = n => String(n).padStart(2, '0');
    return pad(Math.floor(seconds / 3600)) + ':' + pad(Math.floor((seconds % 3600) / 60)) + ':' + pad(seconds % 60);
  }

  function describeSession(s) {
    const length = s.recording ? 'recording now'
      : (s.last_ts ? formatDuration(s.last_ts - s.started) : 'no data');
    return s.name + ' \u00b7 ' + s.sample_count + ' sample' + (s.sample_count === 1 ? '' : 's') + ' \u00b7 ' + length;
  }

  async function recFetch(url, options) {
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch (e) { /* not JSON */ }
    return {ok: res.ok, body: body};
  }

  async function refreshRecorder() {
    let status, sessions;
    try {
      status = await recFetch('/api/recording');
      sessions = await recFetch('/api/recordings');
    } catch (e) {
      return;                                   // server unreachable: leave the display as it is
    }

    if (!status.ok) {
      recEl.status.textContent = 'Recording unavailable';
      recEl.toggle.disabled = true;
      recEl.download.disabled = true;
      recSay(status.body && status.body.error ? status.body.error : '', true);
      return;
    }

    const s = status.body;
    rec.recording = !!s.recording;
    recEl.box.classList.toggle('recording', rec.recording);
    recEl.toggleLabel.textContent = rec.recording ? 'Stop recording' : 'Record';
    recEl.interval.disabled = rec.recording;
    recEl.toggle.disabled = rec.busy;

    if (rec.recording) {
      recEl.status.textContent = 'Recording ' + formatDuration(s.elapsed) + ' \u00b7 '
        + s.samples + ' sample' + (s.samples === 1 ? '' : 's');
      if (rec.sessionId !== s.session_id) { rec.sessionId = s.session_id; rec.userPicked = false; }
      if (Array.from(recEl.interval.options).some(o => o.value === String(s.interval))) {
        recEl.interval.value = String(s.interval);
      }
    } else {
      recEl.status.textContent = 'Not recording';
      rec.sessionId = null;
    }
    if (s.error) recSay('Problem saving readings: ' + s.error, true);

    const list = sessions.ok ? sessions.body : [];
    if (!list.length) {
      if (rec.sessionIds !== 'none') {
        recEl.session.innerHTML = '<option value="">None yet</option>';
        rec.sessionIds = 'none';
      }
    } else {
      const ids = list.map(x => x.id).join(',');
      if (ids !== rec.sessionIds) {
        const previous = recEl.session.value;
        recEl.session.innerHTML = '';
        list.forEach(x => {
          const option = document.createElement('option');
          option.value = String(x.id);
          option.textContent = describeSession(x);
          recEl.session.appendChild(option);
        });
        rec.sessionIds = ids;
        const keep = rec.userPicked && list.some(x => String(x.id) === previous);
        recEl.session.value = keep ? previous
          : (rec.sessionId ? String(rec.sessionId) : String(list[0].id));
      } else {
        Array.from(recEl.session.options).forEach((option, i) => { option.textContent = describeSession(list[i]); });
      }
    }
    recEl.download.disabled = rec.busy || !recEl.session.value;
  }

  recEl.session.addEventListener('change', () => {
    rec.userPicked = true;
    recEl.download.disabled = rec.busy || !recEl.session.value;
  });

  recEl.toggle.addEventListener('click', async () => {
    if (rec.busy) return;
    rec.busy = true;
    recEl.toggle.disabled = true;
    recSay('');
    try {
      const headers = {'Content-Type': 'application/json'};
      const result = rec.recording
        ? await recFetch('/api/recording/stop', {method: 'POST', headers: headers, body: '{}'})
        : await recFetch('/api/recording/start', {method: 'POST', headers: headers,
            body: JSON.stringify({interval: Number(recEl.interval.value)})});
      if (!result.ok) recSay(result.body && result.body.error ? result.body.error : 'That did not work.', true);
    } catch (e) {
      recSay('Could not reach the dashboard server.', true);
    }
    rec.busy = false;
    await refreshRecorder();
  });

  recEl.download.addEventListener('click', async () => {
    const id = recEl.session.value;
    if (!id || rec.busy) return;
    rec.busy = true;
    recEl.download.disabled = true;
    recSay('Preparing the Excel file...');
    try {
      const res = await fetch('/api/recordings/' + id + '/export.xlsx');
      if (!res.ok) {
        let message = 'The download failed.';
        try { const body = await res.json(); if (body.error) message = body.error; } catch (e) { /* keep default */ }
        recSay(message, true);
      } else {
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        let name = 'mulsecube.xlsx';
        const at = disposition.indexOf('filename=');
        if (at !== -1) name = disposition.slice(at + 9).split(';')[0].split('"').join('').trim();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
        recSay('Downloaded ' + name);
      }
    } catch (e) {
      recSay('Could not reach the dashboard server.', true);
    }
    rec.busy = false;
    recEl.download.disabled = !recEl.session.value;
  });

  refreshRecorder();
  setInterval(refreshRecorder, 1000);

  refresh();
  setInterval(refresh, 2000);
  checkPendingSelections();
  setInterval(checkPendingSelections, 1500);
</script>

</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
