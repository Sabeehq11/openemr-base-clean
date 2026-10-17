<?php

/**
 * Pure check: which accounts still use a default or trivially guessable password. No OpenEMR bootstrap, no database,
 * no output. Used by fixture_accounts_audit.php (live accounts) and by an isolated test (synthetic hashes).
 *
 * Only usernames are ever returned. Neither a password nor a hash leaves this function.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

/**
 * Passwords an attacker tries first against a demo or fixture stack, plus ones derived from the username itself.
 *
 * @return list<string>
 */
function copilot_guessable_passwords(string $username): array
{
    $u = strtolower($username);
    return array_values(array_unique([
        'pass', 'admin', 'password', 'Password1', 'password1', 'passw0rd', '12345678', '123456789', 'changeme',
        'openemr', 'OpenEMR123', 'copilot', 'Copilot123', 'copilot123', 'root', 'test', 'demo', 'welcome',
        $username, $u, strrev($u), $u . '1', $u . '123', ucfirst($u) . '1', ucfirst($u) . '123',
    ]));
}

/**
 * @param iterable<array{username: string, hash: string}> $accounts
 * @return list<string> usernames whose stored hash is empty or verifies against a guessable password
 */
function copilot_weak_password_users(iterable $accounts): array
{
    $weak = [];
    foreach ($accounts as $account) {
        $hash = $account['hash'];
        if ($hash === '') {
            $weak[] = $account['username'];
            continue;
        }
        foreach (copilot_guessable_passwords($account['username']) as $guess) {
            if (password_verify($guess, $hash)) {
                $weak[] = $account['username'];
                break;
            }
        }
    }
    return $weak;
}
