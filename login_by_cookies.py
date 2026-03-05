import argparse
import json
import pathlib
import time

from selenium import webdriver


TARGET = "https://www.facebook.com/"


def load_cookies(path: pathlib.Path):
    data = json.loads(path.read_text())
    normalized = []
    for raw in data:
        cookie = {
            "name": raw["name"],
            "value": raw["value"],
            "path": raw.get("path", "/"),
            "domain": raw.get("domain", "").lstrip("."),  # selenium disallows leading dot
            "secure": raw.get("secure", False),
            "httpOnly": raw.get("httpOnly", False),
        }
        if "expirationDate" in raw:
            cookie["expiry"] = int(raw["expirationDate"])
        if same_site := raw.get("sameSite"):
            mapping = {
                "no_restriction": "None",
                "lax": "Lax",
                "strict": "Strict",
                "unspecified": None,
            }
            translated = mapping.get(same_site, same_site)
            if translated:
                cookie["sameSite"] = translated
        normalized.append(cookie)
    return normalized


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cookies",
        default=str(pathlib.Path(__file__).parent / "cookies.json"),
        help="Path to cookies file (default: %(default)s)",
    )
    parser.add_argument(
        "--user-data-dir",
        default="/home/seluser/profiles/ChromeProfile",
        help="Chrome profile dir inside container (default: %(default)s)",
    )
    parser.add_argument(
        "--remote-url",
        default="http://localhost:4444/wd/hub",
        help="Selenium Grid URL (default: %(default)s)",
    )
    args = parser.parse_args()

    opts = webdriver.ChromeOptions()
    if args.user_data_dir:
        opts.add_argument(f"--user-data-dir={args.user_data_dir}")

    driver = webdriver.Remote(args.remote_url, options=opts)
    driver.get(TARGET)  # must land on domain before adding cookies

    cookies = load_cookies(pathlib.Path(args.cookies))
    for c in cookies:
        try:
            driver.add_cookie(c)
        except Exception as exc:
            print(f"Skip cookie {c.get('name')}: {exc}")

    driver.get(TARGET)
    time.sleep(5)


if __name__ == "__main__":
    main()
