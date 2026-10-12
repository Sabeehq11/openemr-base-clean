<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

use OpenEMR\Modules\Copilot\PatientUuidResolver;
use OpenEMR\Modules\Copilot\RequestSigner;
use PHPUnit\Framework\Attributes\DataProvider;
use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/RequestSigner.php';
require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/PatientUuidResolver.php';

final class CopilotModuleIsolatedTest extends TestCase
{
    /** Same vector as agent/tests/test_gateway_unit.py::test_signing_known_vector_for_php_parity. */
    private const PARITY_SIGNATURE = 'cf88ef48d4086ff34f1c1477a273b6f76b4e97b2d51d7a24e1c351d0048700ca';

    public function testSignatureMatchesThePythonAgentByteForByte(): void
    {
        $signer = new RequestSigner('secret');
        $sig = $signer->sign('POST', '/v1/tools/get_problems', '1800000000', 'nonce-0123456789ab', '{"patient_uuid":"p"}', 'user-uuid', 'tok');
        $this->assertSame(self::PARITY_SIGNATURE, $sig);
    }

    public function testMethodIsCaseInsensitiveButBodyPathAndTokenAreBound(): void
    {
        $s = new RequestSigner('k');
        $base = $s->sign('POST', '/p', '1', 'n', 'b', 'u', 't');
        $this->assertSame($base, $s->sign('post', '/p', '1', 'n', 'b', 'u', 't'));
        foreach ([['POST', '/q', '1', 'n', 'b', 'u', 't'], ['POST', '/p', '1', 'n', 'B', 'u', 't'], ['POST', '/p', '1', 'n', 'b', 'u', 'T'], ['POST', '/p', '2', 'n', 'b', 'u', 't']] as $args) {
            $this->assertNotSame($base, $s->sign(...$args));
        }
    }

    public function testDifferentSecretsProduceDifferentSignatures(): void
    {
        $this->assertNotSame(
            (new RequestSigner('a'))->sign('POST', '/p', '1', 'n', 'b', 'u', 't'),
            (new RequestSigner('b'))->sign('POST', '/p', '1', 'n', 'b', 'u', 't'),
        );
    }

    public function testEmptySecretIsRefused(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        new RequestSigner('');
    }

    public function testHeadersCarryEverythingTheGatewayRequires(): void
    {
        $h = (new RequestSigner('secret'))->headers('POST', '/v1/chat', '{}', 'user-uuid', 'tok', 1800000000, 'nonce-0123456789ab');
        $this->assertSame(['x-copilot-timestamp', 'x-copilot-nonce', 'x-copilot-signature', 'x-copilot-user', 'x-copilot-token', 'content-type'], array_keys($h));
        $this->assertSame('1800000000', $h['x-copilot-timestamp']);
    }

    public function testPidResolvesToCanonicalUuid(): void
    {
        $bin = hex2bin('11111111222233334444555555555555');
        $r = new PatientUuidResolver(fn (int $pid): ?string => $pid === 100 ? $bin : null);
        $this->assertSame('11111111-2222-3333-4444-555555555555', $r->resolve(100));
        $this->assertSame('11111111-2222-3333-4444-555555555555', $r->resolve('100'));
    }

    /** @return array<string, array{mixed}> @codeCoverageIgnore Data providers run before coverage instrumentation starts. */
    public static function badPidProvider(): array
    {
        return [
            'null' => [null], 'zero' => [0], 'negative' => [-4], 'empty string' => [''], 'text' => ['abc'],
            'sql-ish' => ['1 OR 1=1'], 'float' => [1.5], 'array' => [[100]], 'unknown pid' => [999],
        ];
    }

    #[DataProvider('badPidProvider')]
    public function testInvalidOrUnknownPidResolvesToNull(mixed $pid): void
    {
        $r = new PatientUuidResolver(fn (int $p): ?string => $p === 100 ? hex2bin('11111111222233334444555555555555') : null);
        $this->assertNull($r->resolve($pid));
    }

    public function testMalformedStoredUuidIsRejected(): void
    {
        $this->assertNull((new PatientUuidResolver(fn (int $p): ?string => 'short'))->resolve(100));
    }
}
