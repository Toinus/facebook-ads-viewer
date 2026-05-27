#!/bin/bash
cd "$(dirname "$0")"
pip3 install flask requests -q 2>/dev/null
python3 "$(dirname "$0")/app_local.py"
