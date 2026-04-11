import json
from pathlib import Path

import requests

url = "https://www.facebook.com/api/graphql/"

headers = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Cookie": "locale=vi_VN; c_user=XXX; xs=XXX;"
}

data = {
    "doc_id": "4430099110431117",
    "variables": '{"groupID":"804362789744484","scale":4,"useCometPhotoViewerPlaceholderFrag":false}'
}

res = requests.post(url, headers=headers, data=data)
res.raise_for_status()

output_path = Path(__file__).with_name("group_info.json")

try:
    response_data = res.json()
    output_path.write_text(
        json.dumps(response_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
except ValueError:
    output_path.write_text(res.text, encoding="utf-8")

print(f"Saved response to {output_path}")
