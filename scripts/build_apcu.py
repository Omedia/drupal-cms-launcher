#!/usr/bin/env python3
"""Build the pinned APCu extension against the same PHP used by the bundle."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
VERSION = "5.1.28"
COMMIT = "819fce90c469a5666b1706036cd66120af419f1f"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/resources/php/lib/apcu.so")
    args = parser.parse_args()
    source = ROOT / "build/apcu-source"
    php = ROOT / "build/resources/php/bin/php"
    php_config = shutil.which("php-config")
    if not php_config:
        raise SystemExit("php-config and phpize are required on the build machine")
    installed = subprocess.check_output([php_config, "--version"], text=True).strip()
    bundled = subprocess.check_output([str(php), "-n", "-r", "echo PHP_VERSION;"], text=True).strip()
    if installed != bundled:
        raise SystemExit(f"PHP headers ({installed}) do not match bundled PHP ({bundled})")
    if not source.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", f"v{VERSION}", "https://github.com/krakjoe/apcu.git", str(source)], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != COMMIT:
        raise SystemExit("APCu source does not match the pinned release commit")
    environment = os.environ.copy()
    pcre_include = Path(php_config).parent.parent / "opt/pcre2/include"
    if (pcre_include / "pcre2.h").is_file():
        environment["CPPFLAGS"] = environment.get("CPPFLAGS", "") + f" -I{pcre_include}"
    subprocess.run(["phpize"], cwd=source, check=True, env=environment)
    subprocess.run(["./configure", "--enable-apcu", f"--with-php-config={php_config}"], cwd=source, check=True, env=environment)
    subprocess.run(["make", "-j4"], cwd=source, check=True, env=environment)
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "modules/apcu.so", args.output)
    subprocess.run(["strip", "-S", str(args.output)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(args.output)], check=True)
    subprocess.run([str(php), "-n", "-d", f"extension={args.output}", "-d", "apc.enable_cli=1", "-r", "apcu_store('build_check', 42); if (apcu_fetch('build_check') !== 42) exit(1); echo 'APCu ', phpversion('apcu'), ' verified', PHP_EOL;"], check=True)
    shutil.copy2(source / "LICENSE", args.output.parent / "APCU-LICENSE")


if __name__ == "__main__":
    main()
