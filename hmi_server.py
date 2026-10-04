import time
from collections import deque
from flask import Flask, request, jsonify

app = Flask(__name__)

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

  function formatSecondsAgo(seconds) {
    if (seconds <= 0) return 'now';
    if (seconds < 60) return `-${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return secs ? `-${mins}m${secs}s` : `-${mins}m`;
  }

  function drawLineChart(canvas, points, minRange, maxRange, unit) {
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

    let dataMin = Math.min(...values);
    let dataMax = Math.max(...values);
    let axisMin = hasRange ? Math.min(dataMin, minRange) : dataMin;
    let axisMax = hasRange ? Math.max(dataMax, maxRange) : dataMax;

    const spread = (axisMax - axisMin) || 1;
    axisMin -= spread * 0.12;
    axisMax += spread * 0.12;

    const yTicks = niceTicks(axisMin, axisMax, 5);
    const tickMin = yTicks[0];
    const tickMax = yTicks[yTicks.length - 1];

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
      drawLineChart(canvas, points, minRange, maxRange, unit);
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
