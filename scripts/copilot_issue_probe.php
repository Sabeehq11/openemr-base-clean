<?php

/**
 * Probe: mint a Co-Pilot user token for a given user UUID from CLI and print it (synthetic data, local dev only).
 * Usage (in container, as apache): php scripts/copilot_issue_probe.php <client-id> <user-uuid>
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli' || $argc < 3) {
    exit(1);
}

$_GET['site'] = 'default';
$ignoreAuth = true;
require_once __DIR__ . '/../interface/globals.php';
require_once __DIR__ . '/../interface/modules/custom_modules/oe-module-copilot/src/UserTokenIssuer.php';

use OpenEMR\Modules\Copilot\UserTokenIssuer;

$siteDir = $GLOBALS['OE_SITE_DIR'] ?? (__DIR__ . '/../sites/default');
echo (new UserTokenIssuer($argv[1], $siteDir))->issue($argv[2]), "\n";
