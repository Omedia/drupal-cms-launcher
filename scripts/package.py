#!/usr/bin/env python3
"""Assemble a self-contained macOS application and a drag-to-install disk image."""

import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "Drupal CMS Launcher.app"
MACH_O_MAGIC = {bytes.fromhex(magic) for magic in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe")}


def version():
    """The tag being released, or the version in Cargo.toml for local builds."""
    tagged = os.environ.get("DRUPAL_CMS_VERSION", "").lstrip("v")
    if tagged:
        return tagged
    declared = re.search(r'^version = "([^"]+)"', (ROOT / "Cargo.toml").read_text(), re.M)
    return declared.group(1)


def minimum_macos(contents):
    minimum = (14, 0)
    for path in contents.rglob("*"):
        if path.is_file() and (path.suffix in {".dylib", ".so"} or path.name in {"php", "drupal-cms-launcher"}):
            result = subprocess.run(["otool", "-l", str(path)], capture_output=True, text=True, check=True)
            for value in re.findall(r"\bminos (\d+(?:\.\d+)+)", result.stdout):
                minimum = max(minimum, tuple(map(int, value.split("."))))
    return ".".join(map(str, minimum))


def main():
    binary = ROOT / "target/release/drupal-cms-launcher"
    resources = ROOT / "build/resources"
    icon = ROOT / "resources/AppIcon.icns"
    for path in [binary, icon, resources / "manifest.json", resources / "site.asif", resources / "php/lib/apcu.so"]:
        if not path.is_file():
            raise SystemExit(f"Build input missing: {path}")
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    shutil.copy2(binary, contents / "MacOS/drupal-cms-launcher")
    target = contents / "Resources"
    target.mkdir()
    for name in ("manifest.json", "site.asif", "router.php", "settings.php"):
        shutil.copy2(resources / name, target / name)
    shutil.copytree(resources / "php", target / "php", symlinks=True)
    shutil.copy2(ROOT / "THIRD_PARTY.md", target / "THIRD_PARTY.md")
    shutil.copy2(icon, target / "AppIcon.icns")
    plist = {
        "CFBundleName": "Drupal CMS Launcher", "CFBundleDisplayName": "Drupal CMS Launcher",
        "CFBundleIdentifier": "ge.omedia.drupal-cms-launcher", "CFBundleExecutable": "drupal-cms-launcher",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": version(),
        "CFBundleIconFile": "AppIcon",
        "CFBundleVersion": version(), "LSMinimumSystemVersion": minimum_macos(contents),
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(plist, output)
    identity = os.environ.get("DRUPAL_CMS_SIGN_IDENTITY", "-")
    command = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        # Notarization requires every nested Mach-O to carry the Developer ID
        # signature with hardened runtime; sign them inside-out, then the app.
        command += ["--timestamp", "--options", "runtime"]
        for path in sorted(contents.rglob("*"), key=lambda item: -len(item.parts)):
            if path.is_file() and not path.is_symlink() and path.open("rb").read(4) in MACH_O_MAGIC:
                # PCRE's JIT needs executable memory, which the hardened runtime denies without this entitlement.
                entitlements = ["--entitlements", str(ROOT / "resources/php.entitlements")] if path.name == "php" else []
                subprocess.run([*command, *entitlements, str(path)], check=True, capture_output=True)
    subprocess.run([*command, str(APP)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    subprocess.run([str(target / "php/bin/php"), "-n", "--version"], check=True)
    subprocess.run([str(target / "php/bin/php"), "-n", "-d", f"extension={target / 'php/lib/apcu.so'}", "-d", "apc.enable_cli=1", "-r", "if (!apcu_store('package_check', 42) || apcu_fetch('package_check') !== 42) exit(1);"], check=True)
    image = DIST / "Drupal-CMS-Launcher-macOS-arm64.dmg"
    staging = DIST / "disk-image"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    subprocess.run(["/usr/bin/ditto", str(APP), str(staging / APP.name)], check=True)
    (staging / "Applications").symlink_to("/Applications")
    subprocess.run(["hdiutil", "create", "-volname", "Drupal CMS Launcher", "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(image)], check=True)
    shutil.rmtree(staging)
    print(f"Built {APP}\nBuilt {image}")


if __name__ == "__main__":
    main()
