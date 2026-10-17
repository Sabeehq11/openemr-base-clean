<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

use OpenEMR\Modules\Copilot\ChatProxy;
use OpenEMR\Modules\Copilot\PatientUuidResolver;
use OpenEMR\Modules\Copilot\RequestSigner;
use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/RequestSigner.php';
require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/PatientUuidResolver.php';
require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/ChatProxy.php';

final class ChatProxyIsolatedTest extends TestCase
{
    private const USER = 'aaaaaaaa-1111-2222-3333-444444444444';
    private const PATIENT = '11111111-2222-3333-4444-555555555555';

    /** @var list<array{url: string, headers: array<string,string>, body: string}> */
    private array $sent = [];

    private function proxy(int $agentStatus = 200, bool $tokenFails = false, bool $w2Chat = false): ChatProxy
    {
        $this->sent = [];
        return new ChatProxy(
            new RequestSigner('secret'),
            new PatientUuidResolver(fn (int $p): ?string => $p === 100 ? hex2bin('11111111222233334444555555555555') : null),
            'http://agent:8400/',
            function (string $u) use ($tokenFails): string {
                if ($tokenFails) {
                    throw new \RuntimeException('no client');
                }
                return 'tok-for-' . $u;
            },
            function (string $url, array $headers, string $body, callable $onChunk) use ($agentStatus): int {
                $this->sent[] = ['url' => $url, 'headers' => $headers, 'body' => $body];
                $onChunk("event: end\ndata: {}\n\n");
                return $agentStatus;
            },
            fn (): string => 'nonce-0123456789ab',
            fn (): int => 1800000000,
            $w2Chat,
        );
    }

    public function testW2ChatPathIsSelectedByConfigAndSignedForThatPath(): void
    {
        $emit = function (string $c): void {
        };
        $this->assertSame(['status' => 200], $this->proxy(w2Chat: true)->handle($this->session(), ['question' => 'brief'], 'corr-12345678', $emit));
        $this->assertSame('http://agent:8400/v1/w2/chat', $this->sent[0]['url']);
        $h = $this->sent[0]['headers'];
        $expected = (new RequestSigner('secret'))->sign('POST', '/v1/w2/chat', '1800000000', 'nonce-0123456789ab', $this->sent[0]['body'], self::USER, $h['x-copilot-token']);
        $this->assertSame($expected, $h['x-copilot-signature']);
    }

    public function testBrowserInputCannotSelectTheW2Path(): void
    {
        $emit = function (string $c): void {
        };
        $this->proxy()->handle($this->session(), ['question' => 'brief', 'path' => '/v1/w2/chat', 'w2_chat' => true], 'corr-12345678', $emit);
        $this->assertSame('http://agent:8400/v1/chat', $this->sent[0]['url']);
    }

    /** @return array{userUuid: ?string, pid: mixed, csrfOk: bool} */
    private function session(array $override = []): array
    {
        return $override + ['userUuid' => self::USER, 'pid' => 100, 'csrfOk' => true];
    }

    public function testHappyPathSignsAndForwardsSessionDerivedIdentity(): void
    {
        $chunks = [];
        $r = $this->proxy()->handle($this->session(), ['question' => 'brief'], 'corr-12345678', function (string $c) use (&$chunks): void {
            $chunks[] = $c;
        });
        $this->assertSame(['status' => 200], $r);
        $this->assertCount(1, $chunks);
        $this->assertSame('http://agent:8400/v1/chat', $this->sent[0]['url']);
        $h = $this->sent[0]['headers'];
        $this->assertSame(self::USER, $h['x-copilot-user']);
        $this->assertSame('tok-for-' . self::USER, $h['x-copilot-token']);
        $this->assertSame('corr-12345678', $h['x-correlation-id']);
        $expected = (new RequestSigner('secret'))->sign('POST', '/v1/chat', '1800000000', 'nonce-0123456789ab', $this->sent[0]['body'], self::USER, $h['x-copilot-token']);
        $this->assertSame($expected, $h['x-copilot-signature']);
        $this->assertSame(self::PATIENT, json_decode($this->sent[0]['body'], true)['patient_uuid']);
    }

    public function testBrowserCannotChooseThePatientOrTheUser(): void
    {
        $this->proxy()->handle(
            $this->session(),
            ['question' => 'x', 'patient_uuid' => '99999999-0000-0000-0000-000000000000', 'pid' => 101, 'user' => 'evil'],
            'c',
            fn (string $c) => null,
        );
        $body = json_decode($this->sent[0]['body'], true);
        $this->assertSame(self::PATIENT, $body['patient_uuid']);
        $this->assertSame(['patient_uuid', 'question', 'conversation_id'], array_keys($body));
        $this->assertSame(self::USER, $this->sent[0]['headers']['x-copilot-user']);
    }

    public function testCsrfFailureStopsBeforeAnyOutboundCall(): void
    {
        $r = $this->proxy()->handle($this->session(['csrfOk' => false]), ['question' => 'x'], 'c', fn ($c) => null);
        $this->assertSame(403, $r['status']);
        $this->assertSame([], $this->sent);
    }

    public function testNoUserNoPatientAndBadQuestionAreRefusedLocally(): void
    {
        $p = $this->proxy();
        $this->assertSame(401, $p->handle($this->session(['userUuid' => null]), ['question' => 'x'], 'c', fn ($c) => null)['status']);
        $this->assertSame(409, $p->handle($this->session(['pid' => null]), ['question' => 'x'], 'c', fn ($c) => null)['status']);
        $this->assertSame(409, $p->handle($this->session(['pid' => 999]), ['question' => 'x'], 'c', fn ($c) => null)['status']);
        $this->assertSame(400, $p->handle($this->session(), ['question' => ''], 'c', fn ($c) => null)['status']);
        $this->assertSame(400, $p->handle($this->session(), ['question' => str_repeat('a', 1001)], 'c', fn ($c) => null)['status']);
        $this->assertSame(400, $p->handle($this->session(), ['question' => ['x']], 'c', fn ($c) => null)['status']);
        $this->assertSame([], $this->sent);
    }

    public function testTokenIssuanceFailureIsAGenericRefusal(): void
    {
        $r = $this->proxy(200, true)->handle($this->session(), ['question' => 'x'], 'c', fn ($c) => null);
        $this->assertSame(['status' => 503, 'error' => 'token_unavailable'], $r);
        $this->assertSame([], $this->sent);
    }

    public function testAgentFailuresAreMapped(): void
    {
        $this->assertSame(['status' => 502, 'error' => 'agent_error'], $this->proxy(500)->handle($this->session(), ['question' => 'x'], 'c', fn ($c) => null));
        $this->assertSame(['status' => 503, 'error' => 'agent_unreachable'], $this->proxy(0)->handle($this->session(), ['question' => 'x'], 'c', fn ($c) => null));
    }

    public function testConversationIdIsOnlyForwardedWhenAString(): void
    {
        $this->proxy()->handle($this->session(), ['question' => 'x', 'conversation_id' => ['a']], 'c', fn ($c) => null);
        $this->assertNull(json_decode($this->sent[0]['body'], true)['conversation_id']);
        $this->proxy()->handle($this->session(), ['question' => 'x', 'conversation_id' => 'abc'], 'c', fn ($c) => null);
        $this->assertSame('abc', json_decode($this->sent[0]['body'], true)['conversation_id']);
    }
}
