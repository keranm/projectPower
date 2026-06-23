import logging
import os
from logging.handlers import RotatingFileHandler

from config import cfg


def setup_logger(name: str = "mcnutty") -> logging.Logger:
    os.makedirs(cfg.log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

    fh = RotatingFileHandler(
        os.path.join(cfg.log_dir, "mcnutty.log"),
        maxBytes=cfg.log_max_bytes,
        backupCount=cfg.log_backup_count,
    )
    fh.setFormatter(fmt)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(sh)

    return logger


log = setup_logger()
