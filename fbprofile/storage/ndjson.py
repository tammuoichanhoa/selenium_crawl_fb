# post/v3/storage/ndjson.py
import json
from pathlib import Path
from typing import List, Dict, Any


def append_ndjson(items: List[Dict[str, Any]], output_path: Path):
    if not items:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as f:
        for it in items:
            if it["id"] in (None, ""):
                continue
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
