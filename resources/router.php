<?php

declare(strict_types=1);

$root = realpath(getenv('DRUPAL_CMS_DATA') . '/site/web');
$path = rawurldecode(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) ?: '/');
// PHP's development server ignores .htaccess. Serve only safe static assets,
// and always send executable requests through Drupal's front controller.
if (str_contains($path, "\0") || preg_match('~(^|/)\.~', $path)) {
  http_response_code(404);
  exit;
}
// Once Drupal creates its base tables, index.php can return 404 until the
// installer finishes. Keep opening the installer while its saved task is not
// "done", including when the launcher is restarted partway through setup.
if ($path === '/') {
  $installed = FALSE;
  $sqlite = getenv('DRUPAL_CMS_DATA') . '/database/drupal.sqlite';
  if (is_file($sqlite)) {
    $database = new PDO('sqlite:' . $sqlite, NULL, NULL, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
    // The installer creates key_value part-way through, so it may not exist yet.
    if ($database->query("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'key_value'")->fetchColumn()) {
      $task = $database->query("SELECT value FROM key_value WHERE collection = 'state' AND name = 'install_task'")->fetchColumn();
      $installed = $task === serialize('done');
    }
  }
  if (!$installed) {
    $progress = getenv('DRUPAL_CMS_DATA') . '/installer-progress.json';
    $parameters = is_file($progress) ? json_decode(file_get_contents($progress), TRUE, flags: JSON_THROW_ON_ERROR) : [];
    header('Location: /core/install.php' . ($parameters ? '?' . http_build_query($parameters) : ''), TRUE, 302);
    exit;
  }
}
if ($path === '/core/install.php' && $_SERVER['REQUEST_METHOD'] === 'GET') {
  // Keep completed-step flags when the launcher reopens the installer on a
  // new port; everything else is already in Drupal's database.
  $parameters = ['profile' => 'drupal_cms_installer'];
  if (isset($_GET['langcode']) && is_string($_GET['langcode']) && preg_match('/^[a-z]{2,3}(?:-[a-zA-Z0-9]{2,8})*$/', $_GET['langcode'])) {
    $parameters['langcode'] = $_GET['langcode'];
  }
  foreach (['name', 'template', 'id'] as $key) {
    if (isset($_GET[$key]) && is_string($_GET[$key]) && ctype_digit($_GET[$key])) {
      $parameters[$key] = (int) $_GET[$key];
    }
  }
  if (isset($parameters['id'])) { $parameters['op'] = 'start'; }
  $progress = getenv('DRUPAL_CMS_DATA') . '/installer-progress.json';
  $temporary = $progress . '.' . getmypid();
  if (file_put_contents($temporary, json_encode($parameters, JSON_THROW_ON_ERROR)) === FALSE || !rename($temporary, $progress)) {
    throw new RuntimeException('Could not save installation progress');
  }
}
$file = realpath($root . '/' . ltrim($path, '/'));
$extension = strtolower(pathinfo($path, PATHINFO_EXTENSION));
$static = ['css', 'js', 'wasm', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'svg', 'ico', 'woff', 'woff2', 'ttf', 'otf', 'pdf', 'mp4', 'webm', 'mp3', 'ogg'];
if ($file && str_starts_with($file, $root . DIRECTORY_SEPARATOR) && is_file($file)
    && (in_array($extension, $static, TRUE) || $path === '/robots.txt')) {
  return FALSE;
}
$script = $path === '/core/install.php' ? '/core/install.php' : '/index.php';
$_SERVER['SCRIPT_NAME'] = $script;
$_SERVER['PHP_SELF'] = $script;
$_SERVER['SCRIPT_FILENAME'] = $root . $script;
chdir(dirname($root . $script));
require $root . $script;
