# Architecture

The launcher is a GPUI window that supervises a PHP process serving Drupal CMS
from a disk image. There is no database server, no container and no web server
beyond PHP's own.

## Two processes

The app launches itself a second time with `--supervise`. The window process
draws the interface; the supervisor process owns everything else.

```
window (GPUI)  ──spawn──▶  supervisor  ──spawn──▶  php -S 127.0.0.1:<port>
      ▲                         │
      └──── JSON events ────────┘
           stdin: OPEN | STOP
```

The supervisor writes one JSON object per line on stdout, each with a `phase`, a
human-readable `message`, and a `url` once the site is reachable. The phases are
`preparing`, `starting`, `ready`, `open`, `stopping`, `stopped` and `error`. The
window reads them to update its status line and enable its buttons; it writes
`OPEN` to open the browser and `STOP` to shut down.

The split matters for shutdown. Because the supervisor is a separate process, it
survives the window long enough to stop PHP and detach the site image cleanly,
even if the window crashes. Closing the window's end of the pipe is itself the
stop signal.

## Starting a site

1. **Lock.** The supervisor takes an exclusive lock on `launcher.lock` in the
   data directory, so a second instance refuses to start rather than corrupting
   the first one's site.
2. **Attach the site.** On first launch the bundled `site.asif` is cloned into
   the data directory with `cp -c`, an APFS copy-on-write clone, then attached
   at `site/` with `diskutil image attach`. Subsequent launches attach the
   existing clone.
3. **Serve.** PHP's built-in server starts on an unused loopback port with four
   workers, `resources/router.php` as its router and OPcache and APCu enabled.
4. **Wait.** The supervisor polls the port with a real HTTP request until Drupal
   answers, then reports `ready` with the URL.

Nothing is initialised on a database server, because there isn't one: Drupal
creates its SQLite file during installation.

## The site disk image

The site is a sparse APFS disk image rather than a directory of files. Drupal
CMS and its dependencies come to about 34,000 files, and creating them on first
launch cost several seconds of pure filesystem work. Cloning a single image file
and attaching it takes well under a second, and an attached image reads as fast
as the native volume. [docs/performance.md](performance.md) has the
measurements and the alternatives that were tried.

The image is created at build time by `scripts/build_site.py` and attached
read-write at runtime, so Drupal can write its own files and uploads inside it.

## Routing

`resources/router.php` sits in front of Drupal because PHP's built-in server
ignores `.htaccess`:

- Requests for dotfiles are refused outright.
- A known-safe list of static extensions is served directly from disk, but only
  for paths that resolve inside the site's web root.
- Everything else goes through Drupal's front controller.
- While Drupal's saved install task is not `done`, `/` redirects to
  `core/install.php`, so an interrupted installation resumes where it stopped
  rather than showing a broken site. The installer's progress is kept in
  `installer-progress.json` so a restart on a different port keeps its place.

## Configuration

`resources/settings.php` is required by the site's `settings.php` and points
Drupal at its SQLite file, its private and temporary directories, and a hash
salt generated on first launch. The site's own `sites/default/settings.php`
contains nothing but that `require`, so the launcher can change runtime
configuration without rewriting anything inside the image.

The build also writes `sites/default/site-templates.php`, Drupal CMS's supported
extension point for limiting the installer to templates that are already
bundled, so template selection never needs to download anything.

## Data directory

`~/Library/Application Support/Drupal CMS Launcher/`, created mode `0700`:

| Entry | Contents |
| --- | --- |
| `site.asif` | The site disk image, attached at `site/` while the app runs |
| `database/` | The SQLite database Drupal creates during installation |
| `state.json` | Format version, hash salt and last-used path, mode `0600` |
| `logs/` | PHP and setup logs |
| `private/`, `tmp/` | Drupal's private and temporary file directories |
| `launcher.lock` | Single-instance lock |

`state.json` records a format version. A data directory written by an older
version with a different layout is refused with an explanation rather than
migrated or overwritten; existing data is never deleted to recover from an
error.

The app briefly shipped under a different name. If a data directory exists under
that name and none exists under the current one, it is moved across on startup,
so a site created then is not stranded.

Drupal stores absolute paths in its caches, including the compiled service
container, so a site does not boot from a directory it was not installed in.
`state.json` records the path each run, and when it differs the cache tables are
emptied before PHP starts. That covers the rename above, a backup restored to
another location, and a copy opened under a different user account.

## Timing instrumentation

Setting `DRUPAL_CMS_TIMING_FILE` makes both processes append timing markers as
JSON lines to that file. Nothing is written without it. `scripts/benchmark.py`
uses these markers to attribute startup time to individual phases.
`DRUPAL_CMS_NO_BROWSER=1` suppresses the automatic browser opening, which the
benchmark needs so Playwright can drive the installer itself.
