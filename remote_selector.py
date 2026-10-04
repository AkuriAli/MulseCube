import requests
import time

HMI_BASE_URL = "http://localhost:5000"
POLL_INTERVAL = 1   # seconds between checking for an answer


def wait_for_hmi(timeout=120):
    """
    Blocks until the HMI dashboard server is answering requests, or until
    `timeout` seconds pass. Needed because at boot, main.py can start a
    moment before the dashboard server is listening. Returns True if the
    HMI came up, False if we gave up waiting.
    """
    print("Waiting for the HMI dashboard to come up...")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            requests.get(f"{HMI_BASE_URL}/api/latest", timeout=2)
            print("HMI is up.")
            return True
        except requests.exceptions.RequestException:
            time.sleep(1)

    print("HMI did not come up in time - continuing anyway.")
    return False


def _cancel_selection(request_id):
    """Withdraws a question from the dashboard (e.g. the sensor was unplugged)."""
    try:
        requests.post(f"{HMI_BASE_URL}/api/selection-cancel",
                      json={"request_id": request_id}, timeout=5)
    except requests.exceptions.RequestException:
        pass


def request_selection(request_id, prompt, options, cancel_event=None):
    """
    Registers a question with the HMI dashboard and blocks until someone
    answers it there. Returns the chosen option string, or None if the HMI
    couldn't be reached - or if `cancel_event` (a threading.Event) is set
    while waiting, in which case the question is also withdrawn from the
    dashboard.
    """
    try:
        requests.post(f"{HMI_BASE_URL}/api/selection-request", json={
            "request_id": request_id,
            "prompt": prompt,
            "options": options,
        }, timeout=5)
    except requests.exceptions.RequestException as e:
        print(f"  Could not reach HMI to request selection: {e}")
        return None

    print(f"  Waiting for selection on the dashboard: {prompt}")

    while True:
        if cancel_event is not None and cancel_event.is_set():
            _cancel_selection(request_id)
            return None

        try:
            response = requests.get(
                f"{HMI_BASE_URL}/api/selection-answer/{request_id}", timeout=5
            )
            data = response.json()
            if data.get("answered"):
                return data.get("choice")
        except requests.exceptions.RequestException:
            pass

        if cancel_event is not None:
            cancel_event.wait(POLL_INTERVAL)    # wakes immediately if cancelled
        else:
            time.sleep(POLL_INTERVAL)


def notify_sensor_removed(model):
    """Tells the dashboard a sensor was unplugged so its panels disappear."""
    try:
        requests.post(f"{HMI_BASE_URL}/api/sensor-removed",
                      json={"model": model}, timeout=5)
    except requests.exceptions.RequestException:
        pass


def poll_manual_add_request():
    """True if someone pressed 'Add sensor manually' on the dashboard (clears the flag)."""
    try:
        response = requests.get(f"{HMI_BASE_URL}/api/manual-add", timeout=3)
        return bool(response.json().get("requested"))
    except (requests.exceptions.RequestException, ValueError):
        return False
