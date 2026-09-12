# Third-party software

Drupal CMS Launcher bundles the following. Everything else in this repository is
covered by [LICENSE](LICENSE).

- **Drupal CMS and Drupal core** — GPL-2.0-or-later.
  [Source](https://git.drupalcode.org/project/cms/-/tree/2.1.4). Exact versions
  and source references for every dependency are recorded in
  `site/composer.lock`, and each package's own licence file stays in the
  prepared site. Two local patches are applied during the build and tracked in
  [patches/](patches/).
- **PHP** — PHP License 3.01, with the Zend Engine under the Zend Engine
  License. [Source and licence](https://www.php.net/license/). The build
  relocates the build machine's PHP together with its dynamically linked
  libraries, so a public distribution's notices must also cover those libraries
  at the versions that machine provided. `build/resources/php/lib/` lists them.
- **APCu 5.1.28** — PHP License 3.01.
  [Pinned source](https://github.com/krakjoe/apcu/tree/819fce90c469a5666b1706036cd66120af419f1f).
  Its licence ships beside the extension as `php/lib/APCU-LICENSE`.
- **GPUI Kit and its component libraries** — Apache-2.0.
  [Source](https://github.com/longbridge/gpui-kit). Exact Rust dependencies are
  recorded in `Cargo.lock`.

Composer is a build-time tool. Neither the Composer executable package nor Drush
is included in the distributed app; the Composer-generated autoloader and its
dependency metadata remain part of the Drupal site, as they do in any Composer
project.

The exact Drupal CMS, Drupal core, PHP and APCu versions in a given build are
recorded in `Contents/Resources/manifest.json` inside the app.
