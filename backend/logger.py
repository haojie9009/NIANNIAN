import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_FMT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    # console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(_FMT, _DATE_FMT))

    # file — daily rotation, keep 30 days
    fh = TimedRotatingFileHandler(
        _LOG_DIR / "app.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_FMT, _DATE_FMT))

    # error-only file
    eh = TimedRotatingFileHandler(
        _LOG_DIR / "error.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(logging.Formatter(_FMT, _DATE_FMT))

    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.addHandler(eh)
    logger.propagate = False
    return logger


# module-level loggers
app_logger = _build_logger("niannian.app")
api_logger = _build_logger("niannian.api")
llm_logger = _build_logger("niannian.llm")
svc_logger = _build_logger("niannian.svc")
