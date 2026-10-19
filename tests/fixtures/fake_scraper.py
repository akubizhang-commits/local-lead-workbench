"""Fake google-maps-scraper for process tests; never touches the network.

Accepts the real CLI arguments, appends one JSONL listing every 0.3 s to the
``-results`` file, starts a grandchild process and logs ``start``/``stop`` events.
Configuration comes from the JSON file named by FAKE_SCRAPER_CONFIG (the adapter
passes only a filtered environment to the scraper, so the test shim sets it):
  log         file receiving {"pid", "event", "t"} lines
  seconds     run time before a normal exit (default 3)
  rows        listings to write (default 5)
  marker      text in the grandchild's command line (for ps checks)
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    config_path = os.environ.get("FAKE_SCRAPER_CONFIG")
    config = json.loads(Path(config_path).read_text()) if config_path else {}
    args = sys.argv[1:]
    results = Path(args[args.index("-results") + 1])
    log = config.get("log")

    def record(event):
        if log:
            with open(log, "a") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "event": event, "t": time.time()}) + "\n")

    record("start")
    marker = config.get("marker", "fake-grandchild")
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", marker])

    def stop(*_):
        record("stop")
        grandchild.kill()
        os._exit(143)

    signal.signal(signal.SIGTERM, stop)
    end = time.time() + float(config.get("seconds", 3))
    written = 0
    while time.time() < end:
        if written < int(config.get("rows", 5)):
            row = dict(title=f"Fake Listing {written}", place_id=f"FAKE_{written}",
                       link=f"https://www.google.com/maps/search/?api=1&query_place_id=FAKE_{written}",
                       address=f"{written} Fake St", phone="", web_site="", category="Fake category",
                       latitude=37.3, longitude=-121.9)
            with results.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            written += 1
        time.sleep(0.3)
    record("stop")
    grandchild.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
