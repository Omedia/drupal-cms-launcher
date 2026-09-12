#!/usr/bin/env python3
"""Prepare the bundled PHP runtime. Build-time only; never shipped in the app."""

import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
RUNTIME = BUILD / "resources"


def bundle_php():
    """Relocate the build machine's PHP and all non-system dylibs into the app."""
    php = Path(shutil.which("php") or "")
    if not php.is_file():
        raise RuntimeError("PHP is required on the build machine")
    version = subprocess.check_output([str(php), "-n", "-r", "echo PHP_VERSION;"], text=True)
    if not version.startswith("8.5."):
        raise RuntimeError(f"This build needs PHP 8.5.x; found {version}")
    prefix = RUNTIME / "php"
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    (prefix / "lib").mkdir(exist_ok=True)
    copied = {}

    def copy_binary(original, target):
        original = original.resolve()
        if original in copied:
            return copied[original]
        if target.exists():
            target.unlink()
        shutil.copy2(original, target)
        target.chmod(0o755)
        copied[original] = target
        listing = subprocess.check_output(["otool", "-L", str(original)], text=True)
        for line in listing.splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if dependency.startswith("@loader_path/"):
                source = original.parent / dependency.removeprefix("@loader_path/")
            elif dependency.startswith("@rpath/"):
                name = dependency.removeprefix("@rpath/")
                commands = subprocess.check_output(["otool", "-l", str(original)], text=True)
                rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", commands)
                candidates = [original.parent / name]
                for rpath in rpaths:
                    resolved = rpath.replace("@loader_path", str(original.parent)).replace("@executable_path", str(php.resolve().parent))
                    candidates.append(Path(resolved) / name)
                source = next((path for path in candidates if path.is_file()), None)
                if source is None:
                    raise RuntimeError(f"Cannot resolve {dependency} in {original}")
            elif dependency.startswith("/"):
                source = Path(dependency)
            else:
                raise RuntimeError(f"Cannot relocate {dependency} in {original}")
            source = source.resolve()
            if source == original:
                continue
            destination = prefix / "lib" / source.name
            if destination in copied.values() and copied.get(source) != destination:
                raise RuntimeError(f"Conflicting library names: {source.name}")
            copy_binary(source, destination)
            replacement = "@loader_path/" + os.path.relpath(destination, target.parent)
            subprocess.run(["install_name_tool", "-change", dependency, replacement, str(target)], check=True)
        if target.suffix == ".dylib":
            subprocess.run(["install_name_tool", "-id", "@rpath/" + target.name, str(target)], check=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True, capture_output=True)
        return target

    copy_binary(php, prefix / "bin/php")
    subprocess.run([str(prefix / "bin/php"), "-n", "-r",
                    "foreach (['pdo_sqlite','sqlite3','dom','gd','mbstring','xml','zip','openssl','Zend OPcache'] as $e) {"
                    "if (!extension_loaded($e)) {fwrite(STDERR, 'Missing extension: '.$e.PHP_EOL); exit(1);}}"], check=True)
    print(f"Bundled PHP and {len(copied) - 1} libraries", flush=True)


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This build targets macOS on Apple Silicon.")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    bundle_php()
    subprocess.run([str(RUNTIME / "php/bin/php"), "--version"], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/build_apcu.py")], check=True)
    print(f"Native runtimes ready in {RUNTIME}", flush=True)


if __name__ == "__main__":
    main()
