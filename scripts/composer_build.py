#!/usr/bin/env python3
"""Run build-time Composer with an isolated configuration; never ship this tool."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
environment = os.environ.copy()
environment["COMPOSER_HOME"] = str(ROOT / "build/composer-home")
environment["COMPOSER_CACHE_DIR"] = str(ROOT / "build/composer-cache")
environment["COMPOSER_AUTH"] = "{}"
if shutil.which("gh"):
    result = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"], capture_output=True, text=True)
    if result.returncode == 0:
        environment["COMPOSER_AUTH"] = json.dumps({"github-oauth": {"github.com": result.stdout.strip()}})

raise SystemExit(subprocess.call(["composer", *sys.argv[1:]], env=environment))
