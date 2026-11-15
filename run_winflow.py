import logging
import os
import sys
from pathlib import Path

# Under pythonw.exe on Windows, stdout/stderr are None, which can break libraries
# like tqdm / huggingface_hub that write progress or warnings to stderr.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from winflow.app import main

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        log_path = Path(__file__).resolve().parent / "winflow.log"
        logging.basicConfig(filename=str(log_path), level=logging.ERROR)
        logging.exception("Fatal unhandled exception in WinFlow")
        raise
