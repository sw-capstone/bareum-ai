import json
import signal
import sys
from pathlib import Path


def main() -> int:
    manifest = Path(sys.argv[1])
    job = json.loads(manifest.read_bytes())
    if job["originalFilename"] == "crash":
        return 7
    if job["originalFilename"] == "invalid":
        manifest.with_name("result.json").write_text('{"status":"SUCCEEDED"}')
        return 0
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.stdout.write("READY\n")
    sys.stdout.flush()
    signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
