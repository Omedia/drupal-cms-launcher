#!/usr/bin/env python3
"""Bundle Drupal CMS and dependencies; leave installation to the user's browser."""

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from build_apcu import VERSION as APCU_VERSION

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
RESOURCES = BUILD / "resources"
SITE = BUILD / "installer-site"


def development_test_path(name):
    parts = PurePosixPath(name).parts
    if parts[:3] == ("web", "core", "tests"):
        return True
    if len(parts) > 4 and parts[:2] == ("web", "core") and parts[2] in {"modules", "themes", "profiles"}:
        return parts[4] == "tests"
    return len(parts) > 4 and parts[0] == "web" and parts[1] in {"modules", "themes", "profiles"} and parts[2] == "contrib" and parts[4] == "tests"


def apply_patches():
    """Apply patches/, unless DRUPAL_CMS_SKIP_PATCHES names some to leave out.

    Set it to a comma-separated list of patch file names, or to "all", to build
    without them and re-measure whether they are still needed.
    """
    skip = os.environ.get("DRUPAL_CMS_SKIP_PATCHES", "")
    skip = {name.strip() for name in skip.split(",") if name.strip()}
    for patch in sorted(ROOT.glob("patches/*.patch")):
        if "all" in skip or patch.name in skip:
            print(f"Skipping {patch.name}", flush=True)
            continue
        applied = subprocess.run(["patch", "--dry-run", "--batch", "--forward", "-p1", "-i", str(patch)], cwd=SITE, capture_output=True)
        if applied.returncode == 0:
            subprocess.run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)], cwd=SITE, check=True)
            continue
        reversible = subprocess.run(["patch", "--dry-run", "--batch", "--reverse", "-p1", "-i", str(patch)], cwd=SITE, capture_output=True)
        if reversible.returncode != 0:
            raise RuntimeError(f"Review patches/{patch.name} against the updated Drupal version")


def main():
    apcu = RESOURCES / "php/lib/apcu.so"
    if not apcu.is_file():
        raise RuntimeError("Run scripts/prepare.py to build the bundled APCu extension first")
    for filename in ("router.php", "settings.php"):
        shutil.copy2(ROOT / "resources" / filename, RESOURCES / filename)
    if not SITE.exists():
        shutil.copytree(ROOT / "site", SITE)
    subprocess.run([sys.executable, str(ROOT / "scripts/composer_build.py"), "install", "--no-dev", "--prefer-dist", "--no-interaction", f"--working-dir={SITE}"], check=True)
    if (SITE / "vendor/composer/composer").exists():
        raise RuntimeError("Composer executable package must not be bundled")
    apply_patches()
    (SITE / "config/sync").mkdir(parents=True, exist_ok=True)
    settings = SITE / "web/sites/default/settings.php"
    settings.write_text("<?php\nrequire getenv('DRUPAL_CMS_RESOURCES') . '/settings.php';\n")
    # Official CMS installer extension point: only offer bundled templates.
    (settings.parent / "site-templates.php").write_text("<?php\nreturn [];\n")
    for filename in ("composer", "composer.bat", "composer.phar", "drush", "drush.php", "drush.bat"):
        (SITE / "vendor/bin" / filename).unlink(missing_ok=True)
    # The launcher clones and attaches this image instead of unpacking an
    # archive; see docs/architecture.md.
    image = RESOURCES / "site.asif"
    mount = BUILD / "site-image-mount"
    subprocess.run(["diskutil", "eject", str(mount)], capture_output=True)
    image.unlink(missing_ok=True)
    subprocess.run(["diskutil", "image", "create", "blank", "--format", "ASIF", "--size", "16G", "--volumeName", "Drupal CMS Site", str(image)], check=True, capture_output=True)
    mount.mkdir(exist_ok=True)
    subprocess.run(["diskutil", "image", "attach", "--nobrowse", "--mountPoint", str(mount), str(image)], check=True, capture_output=True)
    try:
        def ignored(directory, names):
            relative = Path(directory).relative_to(SITE)
            return [name for name in names if development_test_path(str(relative / name)) or name in {".git", ".ddev", "node_modules"}]
        shutil.copytree(SITE, mount, ignore=ignored, symlinks=True, dirs_exist_ok=True)
        # Spotlight would otherwise index 34,000 files every time the image is attached.
        (mount / ".metadata_never_index").touch()
    finally:
        subprocess.run(["diskutil", "eject", str(mount)], check=True, capture_output=True)
    mount.rmdir()
    lock = json.loads((SITE / "composer.lock").read_text())
    core_version = next(package["version"] for package in lock["packages"] if package["name"] == "drupal/core")
    manifest = {
        "database": "sqlite",
        "drupal_cms_version": "2.1.4",
        "drupal_core_version": core_version,
        "php_version": subprocess.check_output([str(RESOURCES / "php/bin/php"), "-n", "-r", "echo PHP_VERSION;"], text=True),
        "apcu_version": APCU_VERSION,
    }
    (RESOURCES / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Bundled Drupal CMS with Drupal core {core_version}.", flush=True)


if __name__ == "__main__":
    main()
