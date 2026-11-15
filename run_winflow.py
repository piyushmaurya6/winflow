import logging
import multiprocessing
import os
import sys
from pathlib import Path

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _parse_settings_args(argv: list[str]) -> tuple[int, Path | None]:
    tab = 0
    cfg_path: Path | None = None
    for arg in argv:
        if arg.startswith("--tab="):
            try:
                tab = int(arg.split("=", 1)[1])
            except ValueError:
                tab = 0
        elif arg.startswith("--config="):
            cfg_path = Path(arg.split("=", 1)[1])
    return tab, cfg_path


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from winflow.config import CONFIG_PATH, LOG_PATH

    try:
        if "--settings" in sys.argv:
            from winflow.settings_ui import run_standalone_settings_window

            tab_idx, custom_cfg = _parse_settings_args(sys.argv[1:])
            raise SystemExit(
                run_standalone_settings_window(
                    initial_tab=tab_idx,
                    config_path=custom_cfg or CONFIG_PATH,
                )
            )

        from winflow.app import main

        main()
    except SystemExit:
        raise
    except Exception:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(LOG_PATH), level=logging.ERROR)
        logging.exception("Fatal unhandled exception in WinFlow")
        raise
