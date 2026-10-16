<?php

/**
 * SourceProxy: ownership, ACL, hash-safe page rendering and generic failures for click-to-source.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

use OpenEMR\Modules\Copilot\AgentClient;
use OpenEMR\Modules\Copilot\PatientUuidResolver;
use OpenEMR\Modules\Copilot\RequestSigner;
use OpenEMR\Modules\Copilot\SourceProxy;
use PHPUnit\Framework\TestCase;

$copilotSrc = __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/';
foreach (['RequestSigner', 'PatientUuidResolver', 'AgentClient', 'UploadProxy', 'SourceProxy'] as $f) {
    require_once $copilotSrc . $f . '.php';
}

final class SourceProxyIsolatedTest extends TestCase
{
    private const USER = 'aaaaaaaa-1111-2222-3333-444444444444';
    private const PDF = "%PDF-1.4\nsynthetic";
    private const PNG = "\x89PNG\r\n\x1a\nsynthetic";
    private const OWN = ['pid' => 100, 'deleted' => false];

    /** @var list<array{url: string, body: array<string,mixed>}> */
    private array $sent = [];
    /** @var list<int> */
    private array $read = [];
    private int $agentStatus = 200;
    /** @var array<string,mixed>|null */
    private ?array $agentBody = null;

    /** @param array{pid: int, deleted: bool}|null $info */
    private function proxy(?array $info, ?string $bytes, bool $agentDown = false): SourceProxy
    {
        $this->sent = [];
        $this->read = [];
        $http = function (string $url, array $headers, string $body) use ($agentDown): array {
            $decoded = json_decode($body, true);
            $this->sent[] = ['url' => $url, 'body' => is_array($decoded) ? $decoded : []];
            if ($agentDown) {
                return [0, ''];
            }
            $reply = $this->agentBody ?? ['page' => 1, 'total_pages' => 1, 'mime' => 'image/png',
                'content_b64' => base64_encode(self::PNG)];
            return [$this->agentStatus, json_encode($reply, JSON_THROW_ON_ERROR)];
        };
        $clock = fn (): int => 1800000000;
        $uuid = fn (int $p): ?string => $p === 100 ? hex2bin('11111111222233334444555555555555') : null;
        return new SourceProxy(
            new PatientUuidResolver($uuid),
            new AgentClient(new RequestSigner('secret'), 'http://agent:8400/', $http, fn (): string => 'nonce-0123456789ab', $clock),
            fn (string $u): string => 'tok-' . $u,
            fn (int $id): ?array => $info,
            function (int $id) use ($bytes): ?string {
                $this->read[] = $id;
                return $bytes;
            },
        );
    }

    /**
     * @param array<string, mixed> $o
     * @return array{userUuid: ?string, pid: mixed, aclOk: bool}
     */
    private function session(array $o = []): array
    {
        return $o + ['userUuid' => self::USER, 'pid' => 100, 'aclOk' => true];
    }

    /** @return array{documentId: mixed, page: mixed} */
    private function req(mixed $doc = '5', mixed $page = '1'): array
    {
        return ['documentId' => $doc, 'page' => $page];
    }

    public function testAnotherPatientsDocumentIsNotFoundAndItsBytesAreNeverRead(): void
    {
        // Guards: patient A open, patient B's file requested by guessing a document id.
        $r = $this->proxy(['pid' => 200, 'deleted' => false], self::PDF)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 404, 'error' => 'not_found'], $r);
        $this->assertSame([], $this->read);
        $this->assertSame([], $this->sent);
    }

    public function testDeletedAndMissingDocumentsLookTheSame(): void
    {
        $deleted = $this->proxy(['pid' => 100, 'deleted' => true], self::PDF)->handle($this->session(), $this->req(), 'cid');
        $missing = $this->proxy(null, null)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 404, 'error' => 'not_found'], $deleted);
        $this->assertSame($deleted, $missing);
        $this->assertSame([], $this->read);
    }

    public function testNoAclNoUserAndNoOpenPatientAreRefusedBeforeAnyLookup(): void
    {
        $p = $this->proxy(self::OWN, self::PDF);
        $this->assertSame(403, $p->handle($this->session(['aclOk' => false]), $this->req(), 'cid')['status']);
        $this->assertSame(401, $p->handle($this->session(['userUuid' => null]), $this->req(), 'cid')['status']);
        $this->assertSame(409, $p->handle($this->session(['pid' => null]), $this->req(), 'cid')['status']);
        $this->assertSame(409, $p->handle($this->session(['pid' => 999]), $this->req(), 'cid')['status']);
        $this->assertSame([], $this->read);
    }

    public function testBadDocumentIdsAndPagesAreRejected(): void
    {
        $p = $this->proxy(self::OWN, self::PDF);
        $cases = [['0', '1'], ['abc', '1'], ['5', '0'], ['5', '51'], ['5', '-1'], [null, '1'], ['5', null], ['5', '1.5']];
        foreach ($cases as [$d, $pg]) {
            $this->assertSame(400, $p->handle($this->session(), $this->req($d, $pg), 'cid')['status']);
        }
        $this->assertSame([], $this->read);
    }

    public function testImageIsReturnedAsStoredWithoutCallingTheAgent(): void
    {
        $r = $this->proxy(self::OWN, self::PNG)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 200, 'mime' => 'image/png', 'bytes' => self::PNG], $r);
        $this->assertSame([], $this->sent);
        $page2 = $this->proxy(self::OWN, self::PNG)->handle($this->session(), $this->req('5', '2'), 'cid');
        $this->assertSame(404, $page2['status']);
    }

    public function testPdfPageIsRenderedByTheAgentFromTheStoredBytes(): void
    {
        $r = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req('5', '2'), 'cid');
        $this->assertSame(200, $r['status']);
        $this->assertSame('image/png', $r['mime']);
        $this->assertSame(self::PNG, $r['bytes']);
        $this->assertCount(1, $this->sent);
        $this->assertStringEndsWith('/v1/w2/documents/5/page', $this->sent[0]['url']);
        $this->assertSame(2, $this->sent[0]['body']['page']);
        $this->assertSame('11111111-2222-3333-4444-555555555555', $this->sent[0]['body']['patient_uuid']);
        $this->assertSame(base64_encode(self::PDF), $this->sent[0]['body']['content_b64']);
    }

    public function testAgentFailuresAreGenericAndNeverPassThroughItsMessage(): void
    {
        $this->agentStatus = 400; // hash mismatch or any other refusal
        $this->agentBody = ['error' => 'invalid_request', 'message' => 'secret internal detail'];
        $r = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 502, 'error' => 'agent_error'], $r);

        $this->agentStatus = 403;
        $denied = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 403, 'error' => 'forbidden'], $denied);

        $this->agentStatus = 404;
        $this->agentBody = ['error' => 'not_found', 'message' => 'No such document.']; // the agent's own answer
        $gone = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req('5', '9'), 'cid');
        $this->assertSame(['status' => 404, 'error' => 'not_found'], $gone);

        $down = $this->proxy(self::OWN, self::PDF, true)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 503, 'error' => 'agent_unreachable'], $down);
    }

    public function testAnAgentWithoutThePageRouteIsReportedAsOutdatedNotAsAMissingPage(): void
    {
        // Regression: the running agent image predated POST /v1/w2/documents/{id}/page, so the framework answered
        // 404 {"detail":"Not Found"} for a document that exists and is the open patient's. That used to be passed on
        // as "not_found", so the viewer said the page image was unavailable with nothing pointing at the real cause.
        $this->agentStatus = 404;
        $this->agentBody = ['detail' => 'Not Found'];
        $r = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 502, 'error' => 'agent_outdated'], $r);
        $this->assertSame([5], $this->read, 'ownership passed and the bytes were read before the agent was asked');

        // A 404 with no body at all is the same fault; only the agent's own not_found is a missing page.
        $this->agentBody = [];
        $empty = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 502, 'error' => 'agent_outdated'], $empty);

        $this->agentBody = ['error' => 'not_found', 'message' => 'No such document.'];
        $missing = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 404, 'error' => 'not_found'], $missing);
    }

    public function testAnAgentReplyThatIsNotAnImageIsRefused(): void
    {
        // Guards the agent returning a script where an image is expected.
        $this->agentBody = ['page' => 1, 'total_pages' => 1, 'mime' => 'image/png',
            'content_b64' => base64_encode('<script>x</script>')];
        $r = $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(['status' => 502, 'error' => 'agent_error'], $r);
    }

    public function testUnreadableOrUnsupportedStoredFilesAreNotServed(): void
    {
        $unreadable = $this->proxy(self::OWN, null)->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(404, $unreadable['status']);
        $html = $this->proxy(self::OWN, '<html>nope</html>')->handle($this->session(), $this->req(), 'cid');
        $this->assertSame(415, $html['status']);
        $this->assertSame([], $this->sent);
    }

    public function testLogLinesCarryTheCorrelationIdAndNoContent(): void
    {
        $log = (string) tempnam(sys_get_temp_dir(), 'cplog');
        $old = ini_set('error_log', $log);
        try {
            $this->proxy(self::OWN, self::PDF)->handle($this->session(), $this->req(), 'cid-77');
        } finally {
            ini_set('error_log', (string) $old);
        }
        $text = (string) file_get_contents($log);
        unlink($log);
        $this->assertStringContainsString('"event":"w2_source"', $text);
        $this->assertStringContainsString('cid-77', $text);
        $this->assertStringNotContainsString('11111111', $text);
        $this->assertStringNotContainsString('synthetic', $text);
    }
}
