import os
import shutil
import subprocess

def move_file(src, dst):
    if not os.path.exists(src):
        return
    print(f"Moving {src} to {dst}")
    os.makedirs(os.path.dirname(dst) if not dst.endswith("/") else dst, exist_ok=True)
    try:
        subprocess.run(["git", "mv", src, dst], check=True, capture_output=True)
    except Exception as e:
        shutil.move(src, dst)

# From utils/ to src/core/
move_file("utils/drivers.py", "src/core/driver_factory.py")
move_file("utils/config.py", "src/core/config_parser.py")
move_file("utils/selectors.py", "src/core/selectors.py")
move_file("utils/selector_remote.py", "src/core/selector_remote.py")

# From utils/ to src/utils/
utils_to_move = [
    "cookies.py", "env.py", "logging_setup.py", "pages.py",
    "ports.py", "profile_backup.py", "profiles.py", "proxies.py", "waits.py"
]
for p in utils_to_move:
    move_file(f"utils/{p}", "src/utils/")

# Create __init__.py files
for d in ["src", "src/core", "src/utils", "src/crawler", "src/fbprofile"]:
    init_path = os.path.join(d, "__init__.py")
    if not os.path.exists(init_path):
        with open(init_path, "w") as f:
            pass

# Move crawler engine and extraction
move_file("crawler.py", "src/crawler/engine.py")
move_file("crawl_data.py", "src/crawler/extraction.py")
move_file("login.py", "src/core/login.py")

# Move fbprofile module to src/
move_file("fbprofile", "src/")

# For the remaining utils folder, if it only has __pycache__ and __init__.py we can remove it
if os.path.exists("utils/__init__.py"):
    subprocess.run(["git", "rm", "utils/__init__.py"], check=False)
    
print("Refactoring files complete.")
