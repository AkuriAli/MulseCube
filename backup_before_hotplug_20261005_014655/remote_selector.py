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


def request_selection(request_id, prompt, options):
    """
    Registers a question with the HMI dashboard and blocks until someone
    answers it there. Returns the chosen option string, or None if the
    HMI couldn't be reached at all.
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
        try:
            response = requests.get(
                f"{HMI_BASE_URL}/api/selection-answer/{request_id}", timeout=5
            )
            data = response.json()
            if data.get("answered"):
                return data.get("choice")
        except requests.exceptions.RequestException:
            pass
        time.sleep(POLL_INTERVAL)