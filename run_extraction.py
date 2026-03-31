import sys
import os

# Ensure the root directory is in the Python search path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.crawler.extraction import main

if __name__ == "__main__":
    main()
