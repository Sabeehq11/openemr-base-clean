<?php

/**
 * Read-only audit before the stack is exposed: no active OpenEMR login may still use a default or guessable password.
 *
 * Reads through OpenEMR's own database layer as the web user (no database password is handled here) and prints ONLY
 * usernames and a verdict. Never a password, a hash, or a candidate that matched. Changes nothing.
 *
 *   docker exec -u apache development-easy-light-openemr-1 \
 *     php /var/www/localhost/htdocs/openemr/scripts/copilot/fixture_accounts_audit.php
 *
 * Exit 0: every active account passed. Exit 1: at least one account is weak (or an account has no password record).
 * Exit 2: the audit could not run.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    exit(2);
}

require_once __DIR__ . '/weak_passwords.php';

$_GET['site'] = 'default';
$ignoreAuth = true;
require_once __DIR__ . '/../../interface/globals.php';

// Every active user that can log in. An active user with no users_secure row cannot authenticate with a password at all,
// which is reported as "no password record" rather than treated as safe or weak.
$result = sqlStatement(
    'SELECT u.username AS username, s.password AS hash FROM users u '
    . 'LEFT JOIN users_secure s ON s.username = u.username WHERE u.active = 1 AND u.username <> \'\' ORDER BY u.username'
);
$accounts = [];
$noRecord = [];
while ($row = sqlFetchArray($result)) {
    if (!is_array($row) || !is_string($row['username'] ?? null)) {
        continue;
    }
    if (!is_string($row['hash'] ?? null)) {
        $noRecord[] = $row['username'];
        continue;
    }
    $accounts[] = ['username' => $row['username'], 'hash' => $row['hash']];
}

$weak = copilot_weak_password_users($accounts);
foreach ($accounts as $a) {
    printf("%-22s %s\n", $a['username'], in_array($a['username'], $weak, true) ? 'WEAK (default or guessable)' : 'ok');
}
foreach ($noRecord as $name) {
    printf("%-22s %s\n", $name, 'no password record (cannot log in with a password)');
}
printf("checked %d active account(s); weak: %d\n", count($accounts), count($weak));
exit($weak === [] ? 0 : 1);
