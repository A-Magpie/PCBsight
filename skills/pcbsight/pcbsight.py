"""
pcbsight.py - Root entry point for PCBsight skill tools.
Run:
    python pcbsight.py inspect <file.pcbdoc>
    python pcbsight.py rules <file.pcbdoc>
    python pcbsight.py layers <file.pcbdoc>
    python pcbsight.py report <file.pcbdoc> --export-all
"""

import sys
import os

# Add scripts directory to path
scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from cli import main

if __name__ == "__main__":
    main()
