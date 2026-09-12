<?php

declare(strict_types=1);

$data = getenv('DRUPAL_CMS_DATA');
if (!$data) {
  throw new RuntimeException('Start this site using Drupal CMS Launcher.');
}
$state = json_decode(file_get_contents($data . '/state.json'), TRUE, flags: JSON_THROW_ON_ERROR);
$databases['default']['default'] = [
  'driver' => 'sqlite',
  'namespace' => 'Drupal\\sqlite\\Driver\\Database\\sqlite',
  'autoload' => 'core/modules/sqlite/src/Driver/Database/sqlite/',
  'database' => $data . '/database/drupal.sqlite',
];
$settings['hash_salt'] = $state['hash_salt'];
$settings['trusted_host_patterns'] = ['^127\.0\.0\.1$', '^localhost$'];
$settings['file_private_path'] = $data . '/private';
$settings['file_temp_path'] = $data . '/tmp';
$settings['config_sync_directory'] = '../config/sync';
$settings['update_free_access'] = FALSE;
// The bundled themes' SVG icon sets are almost half of the directories
// extension discovery walks, and never contain extensions.
$settings['file_scan_ignore_directories'] = ['node_modules', 'bower_components', 'icons'];
$config['automated_cron.settings']['interval'] = 0;
