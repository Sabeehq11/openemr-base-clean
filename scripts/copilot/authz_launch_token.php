<?php

/**
 * Prints an OpenEMR SMART EHR launch token bound to one patient UUID (probe/test helper, synthetic data only).
 * Usage (inside the container, as apache): php scripts/copilot/authz_launch_token.php <patient-uuid>
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli' || $argc < 2) {
    exit(1);
}

$_GET['site'] = 'default';
$ignoreAuth = true;
require_once __DIR__ . '/../../interface/globals.php';

use OpenEMR\FHIR\SMART\SMARTLaunchToken;

echo (new SMARTLaunchToken($argv[1]))->serialize(), "\n";
