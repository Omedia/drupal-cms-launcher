# Contributing

Issues and pull requests are welcome. This document covers building Drupal CMS Launcher
from source and running its checks.

## Prerequisites

The build targets Apple Silicon and macOS 26 or later, and needs:

- Xcode with the Metal toolchain (GPUI compiles shaders)
- Rust (stable) and Python 3.11+
- PHP 8.5.x with Drupal's extensions and development headers (`phpize`,
  `php-config`, PCRE headers), plus Autoconf and Make for building APCu
- Composer and Git

`brew install php composer autoconf pcre2` covers the PHP side.

## Build

```sh
python3 scripts/prepare.py     # bundle PHP and build APCu
python3 scripts/build_site.py  # resolve Drupal CMS, write the site disk image
cargo build --release
python3 scripts/package.py     # assemble the .app and .dmg
```

The result is `dist/Drupal CMS Launcher.app` and
`dist/Drupal-CMS-Launcher-macOS-arm64.dmg`.

Each script is independent, so you can re-run only what changed. `prepare.py`
and `build_site.py` write to `build/`, which is not tracked.

The bundled PHP is relocated from the build machine's own PHP, with its
non-system dynamic libraries copied alongside and their install names rewritten.
At runtime `PATH` contains only the bundled `php/bin` and system directories, so
a developer's Homebrew PHP cannot accidentally satisfy a runtime requirement.

## Checks

```sh
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --lib
python3 tests/smoke.py                 # against the development build
python3 tests/smoke.py \
  'dist/Drupal CMS Launcher.app/Contents/MacOS/drupal-cms-launcher' \
  'dist/Drupal CMS Launcher.app/Contents/Resources'   # against the packaged app
```

The smoke test blocks outgoing internet access in the launched runtime while
allowing loopback. It checks that a first launch reaches Drupal's site-name
step with no database-configuration step, that bundled templates are offered,
that an unfinished installation survives a restart, that a second instance is
refused, that sensitive files are not served, and that closing the control pipe
shuts everything down. Its data goes under `build/` and is deleted on success.

For the full browser installation, `tests/browser_install.js` drives Playwright
through template selection and account creation.

## Benchmarks

`scripts/benchmark.py` measures a real install end to end: it launches the
packaged app, drives the browser installer with Playwright, and reports
clean-start, install and reopen times.

```sh
python3 scripts/benchmark.py \
  --app "$PWD/dist/Drupal CMS Launcher.app" \
  --runs 3 \
  --output "$PWD/build/benchmarks/my-run"
```

Use a new output directory per run. Add `--php-profile` for per-request CPU and
batch-callback timings. Leave the machine alone while it runs. See
[docs/performance.md](docs/performance.md) for results and method.

## Project layout

| Path | What it is |
| --- | --- |
| `src/` | The Rust app: `ui.rs` is the window, `runtime.rs` the supervisor |
| `resources/` | Files copied into the app: PHP router, Drupal settings, entitlements, icon |
| `scripts/` | Build and benchmark tooling, build-time only |
| `site/` | The Composer project defining which Drupal CMS packages are bundled |
| `patches/` | Tracked patches applied to Drupal during the build |
| `tests/` | Smoke test and Playwright installation test |

The app runs as two processes. The window spawns itself with `--supervise`; the
supervisor owns PHP and the site image and reports progress as JSON lines on
stdout. [docs/architecture.md](docs/architecture.md) has the details.

## Icon

`resources/icon.svg` is the source artwork. `resources/AppIcon.icns` is
generated from it and committed, so an ordinary build needs no SVG renderer.
After editing the artwork, regenerate it:

```sh
brew install librsvg
python3 scripts/build_icon.py
rsvg-convert -w 256 -h 256 resources/icon.svg -o docs/icon.png
```

The script renders the ten sizes macOS asks for, dropping the play mark inside
the droplet at 16 and 32 pixels where it is too small to read.

## Patches

`patches/` holds changes applied to Drupal during the build, each with a reason
in [patches/README.md](patches/README.md). `build_site.py` fails the build if a
patch neither applies nor is already applied, so a Drupal update cannot silently
drop one. Review them whenever you change the pinned Drupal version.

## Releases

Pushing to `main` builds the app on a macOS runner and replaces the `latest`
prerelease with that build, so there is always a current download. Pushing a
`v*` tag publishes a versioned release instead, with generated release notes:

```sh
git tag v0.2.0 && git push origin v0.2.0
```

The tag becomes the app's `CFBundleShortVersionString`; builds without one take
the version from `Cargo.toml`. Both attach the disk image and its SHA-256
checksum, and neither runs until the smoke test has passed.

When the signing secrets are present the workflow signs the app with a Developer
ID certificate and notarizes the disk image. Without them it falls back to an
ad-hoc signature and skips notarization, which is fine for local use but will be
refused by Gatekeeper on other machines. The secrets are `MACOS_CERTIFICATE`
(base64 of a `.p12`), `MACOS_CERTIFICATE_PASSWORD`, `MACOS_SIGNING_IDENTITY`,
and `APPLE_API_KEY`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER_ID` for notarization.

Locally, set `DRUPAL_CMS_SIGN_IDENTITY` to a Developer ID identity to sign a
build the same way:

```sh
DRUPAL_CMS_SIGN_IDENTITY="Developer ID Application: …" python3 scripts/package.py
```
