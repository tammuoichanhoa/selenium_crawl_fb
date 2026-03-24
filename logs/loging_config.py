# =========================
# LOGGING CONFIG (colored single file)
# =========================
import logging, hashlib
from logging.handlers import RotatingFileHandler
from pathlib import Path
from colorama import Fore, Style, init

# init colorama cho Windows
init(autoreset=True)

ROOT_LOG_DIR = Path(__file__).resolve().parent / "logs"
ROOT_LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = ROOT_LOG_DIR / "crawl.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

# Logger chính
logger = logging.getLogger("crawl_sheet1")
logger.setLevel(logging.DEBUG)

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Style.DIM + Fore.WHITE,
        logging.INFO: Fore.CYAN,        # xanh
        logging.WARNING: Fore.YELLOW,   # vàng
        logging.ERROR: Fore.RED,        # đỏ
        logging.CRITICAL: Style.BRIGHT + Fore.RED,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        message = super().format(record)
        return f"{color}{message}{Style.RESET_ALL}"

# Tránh add handler trùng khi reload
if not logger.handlers:
    base_fmt = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # Console có màu
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(ColorFormatter(base_fmt, date_fmt))
    logger.addHandler(sh)

    # File không màu (ghi text sạch)
    fh = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(base_fmt, date_fmt))
    logger.addHandler(fh)

def get_post_logger(postlink: str):
    """Child logger tag theo post, propagate lên logger chính."""
    h = hashlib.md5((postlink or "").encode("utf-8")).hexdigest()[:16]
    l = logging.getLogger(f"crawl_sheet1.post.{h}")
    l.propagate = True
    l.setLevel(logging.DEBUG)
    return l
