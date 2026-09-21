import time
from flask import Flask, request, jsonify

app = Flask(__name__)

# In-memory store: { model: { measurement_type: {value, unit, timestamp} } }
latest_readings = {}

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

    latest_readings.setdefault(model, {})[measurement_type] = {
        "value": data.get("value"),
        "unit": data.get("unit", ""),
        "timestamp": data.get("timestamp", time.time()),
    }

    return jsonify({"status": "ok"}), 200


@app.route("/api/latest")
def get_latest():
    return jsonify(latest_readings)


@app.route("/")
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MulseCube</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@500;600&family=IBM+Plex+Sans:wght@400;500&family=IBM+Plex+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #EDEEE9;
    --panel: #FFFFFF;
    --ink: #1B2430;
    --ink-soft: #5B6472;
    --live: #1F6F78;
    --stale: #B23B3B;
    --hairline: #D7D9D2;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: 'IBM Plex Sans', sans-serif;
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
    font-family: 'Source Serif 4', serif;
    font-weight: 600;
    font-size: 1.75rem;
    margin: 0;
    letter-spacing: 0.01em;
  }

  #summary {
    font-size: 0.9rem;
    color: var(--ink-soft);
  }

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
  }

  .panel.stale { border-top-color: var(--stale); }

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
    font-family: 'IBM Plex Mono', monospace;
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

  #empty {
    padding: 3rem 1rem;
    text-align: center;
    color: var(--ink-soft);
    border: 1px dashed var(--hairline);
  }
</style>
</head>
<body>

<header>
  <h1>MulseCube</h1>
  <div id="summary">Waiting for readings&hellip;</div>
</header>

<div id="grid"></div>
<div id="empty" style="display:none;">No sensors reporting yet. Connect a sensor and confirm it on the Pi to see live values here.</div>

<script>
  const STALE_AFTER_SECONDS = 10;

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

    const now = Date.now() / 1000;
    let panelCount = 0;
    let liveCount = 0;

    for (const [model, measurements] of Object.entries(data)) {
      for (const [type, info] of Object.entries(measurements)) {
        panelCount++;
        const age = now - info.timestamp;
        const stale = age > STALE_AFTER_SECONDS;
        if (!stale) liveCount++;

        const panel = document.createElement('div');
        panel.className = 'panel' + (stale ? ' stale' : '');
        panel.innerHTML = `
          <div class="port-label">${model}</div>
          <div class="measurement">${type.replace(/_/g, ' ')}</div>
          <div class="value">${info.value}${info.unit}</div>
          <div class="status">${stale ? 'No recent data' : 'Live \\u00b7 updated ' + Math.round(age) + 's ago'}</div>
        `;
        grid.appendChild(panel);
      }
    }

    empty.style.display = panelCount === 0 ? 'block' : 'none';
    summary.textContent = panelCount === 0
      ? 'Waiting for readings\\u2026'
      : `${liveCount} of ${panelCount} reading${panelCount === 1 ? '' : 's'} live`;
  }

  refresh();
  setInterval(refresh, 2000);
</script>

</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)