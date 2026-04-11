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
    "doc_id": "4544387022318594",
    "variables": '{"cursor":"AQHRmBn_UJsK3dR-yNSeM5EWqZrUOWENfN4yiKwCpt6ib02x7laEf3BabmJAJqN_1SVcd6Pbdm68LGtr5Ay9v5zlgw","scale":1,"useCometPhotoViewerPlaceholderFrag":false,"id":"263510030791508"}'
}

res = requests.post(url, headers=headers, data=data)
res.raise_for_status()

output_path = Path(__file__).with_name("next_group_info.json")

try:
    response_data = res.json()
    output_path.write_text(
        json.dumps(response_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
except ValueError:
    output_path.write_text(res.text, encoding="utf-8")

print(f"Saved response to {output_path}")
