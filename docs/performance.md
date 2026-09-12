# Performance

First-time setup takes about 21 seconds, down from 68 seconds when the project
first worked end to end. This document records where the time goes now, which
changes bought the difference, and which plausible ideas were measured and
rejected.

All figures are three-run medians on one machine: Apple M2 Max, 12 logical
cores, 32 GiB, macOS 26.5.1, installing the **Byte** template in English.
Ranges are the observed minimum and maximum, not confidence intervals. Three
runs are enough to find large bottlenecks and not enough for tail latency.

## Where the time goes

| Measurement | Median | Range |
| --- | ---: | ---: |
| Clean start: process launch to the first installer page | 2.96 s | 2.71–3.11 |
| Clean install: first page to dashboard, excluding typing | 18.25 s | 17.91–19.70 |
| Reopen an installed site | 3.56 s | 3.44–3.62 |

Clean start breaks down as roughly 0.7 s to clone and attach the site image,
0.2 s to start PHP, and the rest in GPUI startup and Drupal rendering its first
installer page. Installation is almost entirely Drupal's own work: installing
110 modules, importing recipe configuration and sample content, and rendering
the dashboard once on a cold cache.

## What made the difference

| Change | Effect |
| --- | --- |
| Ship the site as a sparse APFS disk image instead of a tar archive | Clean start 11.7 s → 5.1 s |
| Upgrade to Drupal core 11.4 | Install 41 s → 17 s |
| Replace bundled MySQL with SQLite | Clean start 4.9 s → 3.0 s, reopen 4.1 s → 3.6 s |
| Bundle APCu | Install 54 s → 47 s |
| Skip icon directories during extension discovery, enlarge OPcache | Clean start 13.4 s → 11.2 s |
| Patch Drupal core's discovery and batch handling | Install 45.6 s → 41.0 s |
| Drop development test suites from the payload | Extraction 9.0 s → 6.5 s |

Two of these deserve explanation.

**The site disk image.** Drupal CMS and its dependencies are about 34,000 files.
Unpacking them on first launch cost 5–7 seconds of pure filesystem work, and
decompression was only 0.34 s of that. Shipping a prepared APFS image instead
turns first launch into an instant copy-on-write clone plus a sub-second
attach, and an attached image reads as fast as the native volume: walking all
34,000 files took 0.29 s warm against 0.65 s on the native tree, and reading
every file took 1.4 s against 3.6 s.

**Drupal core 11.4.** Installing a recipe's modules one at a time rebuilt
Drupal's service container 110 times, which profiling showed as about half of
all PHP time. Core issue
[#3498026](https://www.drupal.org/project/drupal/issues/3498026) changed the
recipe runner to install modules in batches of 20 with one rebuild each, and
shipped in 11.4. Drupal CMS 2.1.4 pins core to `~11.3.10`, so `site/composer.json`
raises that to `~11.4.0`; Composer resolves the same Drupal CMS packages against
it without conflicts.

## What did not work

| Idea | Result |
| --- | --- |
| Extract the archive with parallel `tar` processes | 6.9–8.1 s against 4.6 s serial; APFS serialises file creation |
| Hard-link the 4,946 duplicate files in the archive | Three times slower to extract |
| zstd instead of gzip | No measurable change; decompression was never the cost |
| Copy an extracted tree with `cp -c` or `ditto` | 6.5 s and 15 s; cloning a tree still creates every inode |
| Run PHP from a symlinked or read-only docroot | PHP resolves `__DIR__` through symlinks, so Drupal cannot find `sites/` ([#2833883](https://www.drupal.org/project/drupal/issues/2833883)) |
| Enlarge the interned-string buffer to 16 MiB | Ranges overlapped; no clear gain |
| WebAssembly PHP, or moving work into Rust | The launcher's own code is well under a second of startup; the rest is Drupal running in native PHP |
| PGlite or Turso instead of SQLite | Neither is smaller once a runtime is bundled, and PGlite multiplexes a single connection behind a socket server |

## Are the patches still worth it?

Both are, on Drupal core 11.4.6. Measured by rebuilding without them
(`DRUPAL_CMS_SKIP_PATCHES`, see [patches/README.md](../patches/README.md)):

| Build | Clean install |
| --- | ---: |
| Both patches applied | 18.25 s (17.91–19.70) |
| Without `drupal-core-install-speed.patch` | 23.61 s (22.50–24.27) |
| Without either patch | Installation fails |

The speed patch is worth about 5.4 s, roughly a quarter of installation time,
even though core 11.4 already cut container rebuilds from 110 to about six. The
compatibility patch is not optional: without it the Byte template still fails
part-way through with the synthetic-kernel service error it was written for.

Clean start and reopen were unchanged in both builds, as expected; the patches
only affect installation.

## Open questions

Installation is about 1.3 s slower on SQLite than it was on MySQL. Drupal's
SQLite driver runs in WAL mode with full synchronisation, so each autocommitted
statement during installation waits for an fsync where MySQL grouped commits.
Setting `PRAGMA synchronous=NORMAL` through the driver's `init_commands` would
probably recover that, and in WAL mode stays consistent across application
crashes, but it has not been measured and trades durability for speed.

## Method

The benchmark launches the real packaged app with timing markers enabled, and
drives Drupal's actual browser installer with Playwright in a fresh browser
session. Each run uses a new data directory. PHP's outgoing internet access is
blocked while loopback stays available.

Excluded: human reading and typing, browser process startup, copying from the
disk image, and Gatekeeper assessment. Clean start includes the first installer
page; clean install starts after it. Measurements are taken on a warm OS cache,
not after a reboot.

To reproduce, see [CONTRIBUTING.md](../CONTRIBUTING.md#benchmarks).
