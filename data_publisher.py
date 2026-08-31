import requests
import json
import os


class DataPublisher:
    """
    Sends readings to the target webapp/HMI. Falls back to a local file
    if the webapp can't be reached, so no data is lost while offline.
    """

    def __init__(self, webapp_url, local_backup_file="unsent_data.json", timeout=3):
        self.webapp_url = webapp_url
        self.local_backup_file = local_backup_file
        self.timeout = timeout

    def publish(self, reading):
        try:
            response = requests.post(self.webapp_url, json=reading, timeout=self.timeout)
            response.raise_for_status()
            print(f"  Sent to webapp: {reading}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"  Could not reach webapp ({e}). Saving locally instead.")
            self._save_locally(reading)
            return False

    def _save_locally(self, reading):
        existing = []
        if os.path.exists(self.local_backup_file):
            with open(self.local_backup_file, "r") as f:
                try:
                    existing = json.load(f)
                except json.JSONDecodeError:
                    existing = []

        existing.append(reading)

        with open(self.local_backup_file, "w") as f:
            json.dump(existing, f, indent=2)