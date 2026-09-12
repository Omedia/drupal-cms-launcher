<?php
// Loaded only into disposable benchmark sites, never into the distributed app.
function cms_benchmark_operation(mixed $callback, array $arguments): mixed {
    $start = hrtime(TRUE);
    $cpu = getrusage();
    try {
        $callable = \Drupal::service(\Drupal\Core\Utility\CallableResolver::class)->getCallableFromDefinition($callback);
        return $callable(...$arguments);
    }
    finally {
        $end = getrusage();
        $name = is_array($callback)
            ? (is_object($callback[0]) ? get_class($callback[0]) : (string) $callback[0]) . '::' . $callback[1]
            : (is_string($callback) ? $callback : get_debug_type($callback));
        $properties = isset($arguments[0]) && is_object($arguments[0]) ? get_object_vars($arguments[0]) : [];
        $record = [
            'kind' => 'operation', 'callback' => $name,
            'extension' => (str_ends_with($name, '::installModule') || str_ends_with($name, '::installTheme')) && isset($arguments[0]) && is_string($arguments[0]) ? $arguments[0] : NULL,
            'recipe' => isset($properties['name']) && is_string($properties['name']) ? $properties['name'] : NULL,
            'modules' => array_values(array_filter($properties['modules'] ?? [], 'is_string')),
            'themes' => array_values(array_filter($properties['themes'] ?? [], 'is_string')),
            'wall_seconds' => (hrtime(TRUE) - $start) / 1e9,
            'user_cpu_seconds' => ($end['ru_utime.tv_sec'] - $cpu['ru_utime.tv_sec']) + ($end['ru_utime.tv_usec'] - $cpu['ru_utime.tv_usec']) / 1e6,
            'system_cpu_seconds' => ($end['ru_stime.tv_sec'] - $cpu['ru_stime.tv_sec']) + ($end['ru_stime.tv_usec'] - $cpu['ru_stime.tv_usec']) / 1e6,
        ];
        file_put_contents(getenv('DRUPAL_CMS_PHP_TIMING_FILE'), json_encode($record, JSON_THROW_ON_ERROR) . "\n", FILE_APPEND | LOCK_EX);
    }
}

(static function (): void {
    $output = getenv('DRUPAL_CMS_PHP_TIMING_FILE');
    if (!$output) { return; }
    $begin = hrtime(TRUE);
    $cpu = getrusage();
    register_shutdown_function(static function () use ($output, $begin, $cpu): void {
        $end = getrusage();
        $status = opcache_get_status(FALSE);
        $record = [
            'kind' => 'request',
            'pid' => getmypid(),
            'path' => parse_url($_SERVER['REQUEST_URI'] ?? '', PHP_URL_PATH),
            'op' => $_GET['op'] ?? NULL,
            'wall_seconds' => (hrtime(TRUE) - $begin) / 1e9,
            'user_cpu_seconds' => ($end['ru_utime.tv_sec'] - $cpu['ru_utime.tv_sec']) + ($end['ru_utime.tv_usec'] - $cpu['ru_utime.tv_usec']) / 1e6,
            'system_cpu_seconds' => ($end['ru_stime.tv_sec'] - $cpu['ru_stime.tv_sec']) + ($end['ru_stime.tv_usec'] - $cpu['ru_stime.tv_usec']) / 1e6,
            'peak_php_bytes' => memory_get_peak_usage(TRUE),
            'included_files' => count(get_included_files()),
            'realpath_cache_bytes' => realpath_cache_size(),
            'realpath_limit' => ini_get('realpath_cache_size'),
            'apcu' => function_exists('apcu_enabled') && apcu_enabled() ? apcu_cache_info(TRUE) : NULL,
            'opcache' => [
                'cache_full' => $status['cache_full'],
                'memory' => $status['memory_usage'],
                'interned_strings' => $status['interned_strings_usage'],
                'statistics' => $status['opcache_statistics'],
            ],
        ];
        file_put_contents($output, json_encode($record, JSON_THROW_ON_ERROR) . "\n", FILE_APPEND | LOCK_EX);
    });
})();
