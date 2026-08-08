import logging
import os
import pathlib
from datetime import datetime

# Written to the file log once per launch; the Activity Log seed (src/ui/state.py)
# shows only lines from the last occurrence, so each session starts unambiguously.
SESSION_MARKER = "========== persona session started"


def setup_logging(
    log_dir: str = "logs",
    log_level: int = logging.INFO,
) -> logging.Logger:
    pathlib.Path(log_dir).mkdir(exist_ok=True, parents=True)

    log_filename = datetime.now().strftime("persona_%Y%m%d.log")
    log_path = os.path.join(log_dir, log_filename)

    # Logs can carry proxy hostnames / IPs; keep the dir + file owner-only on
    # POSIX (chmod is a near-no-op on Windows, harmless). Create the file first
    # so the handler opens an already-0600 file.
    try:
        os.chmod(log_dir, 0o700)
    except OSError:
        pass
    try:
        if not os.path.exists(log_path):
            os.close(os.open(log_path, os.O_CREAT | os.O_WRONLY, 0o600))
        else:
            os.chmod(log_path, 0o600)
    except OSError:
        pass

    logger = logging.getLogger("persona")
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    # The frozen Windows app's stderr is a locale-encoded file (cp1251 on a
    # Russian install); a record with "—" or "…" would then raise inside
    # logging and spam "--- Logging error ---" instead of the message.
    reconfigure = getattr(console_handler.stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    try:
        from ..services.app_update.updater import APP_VERSION as version
    except Exception:
        version = "unknown"
    logger.info("%s %s ==========", SESSION_MARKER, version)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"persona.{name}")
    return logging.getLogger("persona")
