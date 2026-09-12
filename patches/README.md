# Patches

`scripts/build_site.py` applies these to the Drupal code it resolves during the
build. It fails the build if a patch neither applies nor is already applied, so
a Drupal update cannot silently drop one.

Review them whenever the pinned Drupal version changes. To check whether one is
still earning its place, rebuild without it and re-run the benchmark:

```sh
rm -rf build/installer-site
DRUPAL_CMS_SKIP_PATCHES=drupal-core-install-speed.patch python3 scripts/build_site.py
```

The variable takes a comma-separated list of patch file names, or `all`. Both
patches were last verified as necessary against Drupal core 11.4.6; see
[docs/performance.md](../docs/performance.md#are-the-patches-still-worth-it).

## `drupal-cms-helper-current-config-factory.patch`

Changes one service lookup in the Drupal CMS helper module's recipe subscriber.
Installing a theme can rebuild the service container while the subscriber is
still alive, leaving its constructor-injected configuration factory holding
services from the container that was replaced. Looking the factory up when the
recipe event arrives, instead of at construction, avoids that.

Without it, installing the Byte template through the browser fails with a
service error from the installer's synthetic kernel;
`tests/browser_install.js` reproduces the failure and passes with the patch
applied. Related upstream reports:
[#3606822](https://www.drupal.org/project/drupal/issues/3606822) and
[#3564735](https://www.drupal.org/project/drupal/issues/3564735). This is a
local compatibility fix, not a claim that either issue is resolved.

Drupal core is not touched by this patch.

## `drupal-core-install-speed.patch`

Changes three Drupal core files. Profiling an installation showed about half of
all PHP time spent re-reading the same directories after each of the 110 module
installs:

- **`ExtensionDiscovery.php`** keeps each directory scan in APCu, keyed by the
  modification times of the search directory and the directories up to two
  levels below it, so adding, removing or renaming an extension directory still
  invalidates the entry. Without APCu the original scan runs unchanged.
- **`FileCache.php`** asks the kernel for the working directory once per
  request rather than once per relative `realpath()` call, which is comparatively
  expensive on macOS.
- **`batch.inc`** lets a progressive batch request run for five seconds instead
  of one before returning to the browser, so the installer bootstraps Drupal
  about a quarter as often. The progress bar updates less often as a result.

The hunks applied unchanged to Drupal 11.3.16 and 11.4.6. Core 11.4 reduced
container rebuilds from 110 to about six on its own, but the patch is still
worth about 5.4 s of a roughly 18-second installation.
