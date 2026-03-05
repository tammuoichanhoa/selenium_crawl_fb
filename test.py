import argparse
from selenium import webdriver

parser = argparse.ArgumentParser()
parser.add_argument(
    "--user-data-dir",
    default="/home/seluser/ChromeProfile",
    help="Chrome profile path inside container (default: %(default)s)",
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
