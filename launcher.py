"""Entry point used by PyInstaller (Convert.exe)."""
import sys

from convert_win.app import main

if __name__ == "__main__":
    main(sys.argv[1:])
