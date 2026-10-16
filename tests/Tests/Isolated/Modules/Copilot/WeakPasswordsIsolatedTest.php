<?php

/**
 * The pre-exposure account audit: default and guessable passwords are caught, strong ones are not, and nothing but
 * usernames is ever returned. Uses synthetic hashes made here; no database and no real account is read.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../../../../scripts/copilot/weak_passwords.php';

final class WeakPasswordsIsolatedTest extends TestCase
{
    private const STRONG = 'k7-Vq2!mZp9#xLw4Rt';

    /** @return array{username: string, hash: string} */
    private static function account(string $username, string $password): array
    {
        return ['username' => $username, 'hash' => password_hash($password, PASSWORD_BCRYPT, ['cost' => 4])];
    }

    public function testTheStockDefaultPasswordIsCaught(): void
    {
        // Guards: a demo user left on OpenEMR's stock "pass" would be reachable from the public tunnel.
        $this->assertSame(['physician'], copilot_weak_password_users([self::account('physician', 'pass')]));
    }

    public function testPasswordsDerivedFromTheUsernameAreCaught(): void
    {
        $accounts = [
            self::account('clinician', 'clinician'),
            self::account('receptionist', 'receptionist123'),
            self::account('zhportal', 'Zhportal1'),
        ];
        $this->assertSame(['clinician', 'receptionist', 'zhportal'], copilot_weak_password_users($accounts));
    }

    public function testStrongPasswordsPass(): void
    {
        $this->assertSame([], copilot_weak_password_users([
            self::account('copilot_doc_a', self::STRONG),
            self::account('admin', 'another-Long-unique-passphrase-93'),
        ]));
    }

    public function testAnEmptyHashIsReportedAsWeakNotSkipped(): void
    {
        $this->assertSame(['ghost'], copilot_weak_password_users([['username' => 'ghost', 'hash' => '']]));
    }

    public function testOnlyUsernamesComeBackNeverAPasswordOrAHash(): void
    {
        $accounts = [self::account('physician', 'pass'), self::account('copilot_doc_a', self::STRONG)];
        $weak = copilot_weak_password_users($accounts);
        $this->assertSame(['physician'], $weak);
        $serialised = json_encode($weak, JSON_THROW_ON_ERROR);
        foreach ([$accounts[0]['hash'], $accounts[1]['hash'], 'pass', self::STRONG] as $secret) {
            $this->assertStringNotContainsString($secret, str_replace('physician', '', $serialised));
        }
    }

    public function testMixedAccountsReportExactlyTheWeakOnesInOrder(): void
    {
        $accounts = [
            self::account('accountant', 'pass'),
            self::account('copilot_doc_a', self::STRONG),
            self::account('physician', 'physician'),
        ];
        $this->assertSame(['accountant', 'physician'], copilot_weak_password_users($accounts));
    }
}
