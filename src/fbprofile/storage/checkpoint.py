# post/v3/storage/checkpoint.py
import json
from pathlib import Path
from datetime import datetime
from logs.loging_config import logger


def save_checkpoint(checkpoint_path: Path, latest_ts: int):
    try:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    ck = {
        "last_created_time": int(latest_ts),
        "last_created_date": datetime.fromtimestamp(latest_ts).strftime("%Y-%m-%d"),
    }
    with checkpoint_path.open("w", encoding="utf-8") as f:
        json.dump(ck, f, ensure_ascii=False, indent=2)

    logger.info(
        "[CKPT] Saved checkpoint: ts=%s date=%s",
        ck["last_created_time"],
        ck["last_created_date"],
    )
