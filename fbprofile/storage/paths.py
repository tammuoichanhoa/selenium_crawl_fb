# post/v3/storage/paths.py
from pathlib import Path
from ..config import PROJECT_ROOT


def compute_paths(data_root: Path, page_name: str, account_tag: str):
    base = data_root / "profile" / "page" / page_name
    if account_tag:
        base = base / f"ACC_{account_tag}"

    out_ndjson = base / "posts_all.ndjson"
    raw_dump_dir = base / "raw_dump_posts"
    checkpoint = base / "checkpoint.json"

    base.mkdir(parents=True, exist_ok=True)
    raw_dump_dir.mkdir(parents=True, exist_ok=True)
    out_ndjson.parent.mkdir(parents=True, exist_ok=True)
    print(f"--- BẮT ĐẦU QUÉT PROFILE: {page_name} ---")
    return base, out_ndjson, raw_dump_dir, checkpoint
