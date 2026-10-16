<?php

/**
 * Shared fixture for the UploadProxy tests: a proxy wired to fakes, plus the requests it sent.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

use OpenEMR\Modules\Copilot\AgentClient;
use OpenEMR\Modules\Copilot\LabRecordWriter;
use OpenEMR\Modules\Copilot\PatientUuidResolver;
use OpenEMR\Modules\Copilot\RequestSigner;
use OpenEMR\Modules\Copilot\UploadProxy;
use PHPUnit\Framework\TestCase;

$copilotSrc = __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/';
foreach (['RequestSigner', 'PatientUuidResolver', 'AgentClient', 'LabRecordWriter', 'UploadProxy'] as $f) {
    require_once $copilotSrc . $f . '.php';
}
require_once __DIR__ . '/UploadStore.php';

abstract class UploadProxyTestCase extends TestCase
{
    protected const USER = 'aaaaaaaa-1111-2222-3333-444444444444';
    protected const PATIENT = '11111111-2222-3333-4444-555555555555';
    protected const PDF = "%PDF-1.4\nsynthetic";

    /** @var list<array{url: string, headers: array<string,string>, raw: string, body: array<string,mixed>}> */
    protected array $sent = [];
    /** @var list<string> */
    protected array $created = [];
    /** @var list<int> */
    protected array $deleted = [];
    /** Status the fake agent returns for /v1/w2/preflight */
    protected int $preflightStatus = 200;
    /** Status the fake agent returns for /v1/w2/documents/{id}/links */
    protected int $linkStatus = 200;
    protected UploadStore $store;

    /** @return array<string, mixed> */
    protected function document(string $status): array
    {
        $row = static fn (string $n, string $flag, string $support = 'supported'): array => [
            'test_name' => $n, 'value' => '7.9', 'unit' => '%', 'reference_range' => '4-5.6',
            'abnormal_flag' => $flag, 'loinc' => null, 'support' => $support,
        ];
        return ['document_id' => 5, 'patient_uuid' => 'p', 'sha256' => 'h', 'status' => $status, 'lab' => [
            'collection_date' => '2026-09-12',
            'rows' => [$row('Hemoglobin A1c', 'high'), $row('Sodium', 'normal'), $row('Ghost', 'high', 'unsupported')],
        ]];
    }

    protected function proxy(?int $existingDoc = null, int $agentStatus = 200, string $status = 'extracted'): UploadProxy
    {
        $this->sent = [];
        $this->created = [];
        $this->deleted = [];
        $this->store = new UploadStore();
        $http = function (string $url, array $headers, string $body) use ($agentStatus, $status): array {
            $decoded = json_decode($body, true);
            $this->sent[] = ['url' => $url, 'headers' => $headers, 'raw' => $body, 'body' => is_array($decoded) ? $decoded : []];
            if (str_ends_with($url, '/v1/w2/preflight')) {
                return [$this->preflightStatus, $this->preflightStatus === 200 ? '{"ok":true}' : '{"error":"forbidden"}'];
            }
            if (str_ends_with($url, '/v1/w2/documents')) {
                $payload = $agentStatus === 200
                    ? ['document' => $this->document($status), 'duplicate' => false]
                    : ['error' => 'unsupported_type'];
                return [$agentStatus, json_encode($payload, JSON_THROW_ON_ERROR)];
            }
            if (str_ends_with($url, '/links')) {
                return [$this->linkStatus, $this->linkStatus === 200 ? '{"updated":1}' : '{"error":"agent_error"}'];
            }
            return [200, str_ends_with($url, '/retrieve') ? '{"status":"ok","snippets":[]}' : '{"updated":1}'];
        };
        $uuid = fn (int $p): ?string => $p === 100 ? hex2bin('11111111222233334444555555555555') : null;
        $nonce = fn (): string => 'nonce-0123456789ab';
        $clock = fn (): int => 1800000000;
        return new UploadProxy(
            new PatientUuidResolver($uuid),
            new LabRecordWriter($this->store, $clock),
            new AgentClient(new RequestSigner('secret'), 'http://agent:8400/', $http, $nonce, $clock),
            fn (string $u): string => 'tok-' . $u,
            fn (int $pid, string $hash): ?int => $existingDoc,
            function (int $pid, int $cat, string $name, string $mime, string $bytes): int {
                $this->created[] = "$pid:$cat:$name:$mime";
                return 5;
            },
            function (int $id): void {
                $this->deleted[] = $id;
            },
            1000,
        );
    }

    /**
     * @param array<string, mixed> $o
     * @return array{userUuid: ?string, pid: mixed, csrfOk: bool, aclOk: bool}
     */
    protected function session(array $o = []): array
    {
        return $o + ['userUuid' => self::USER, 'pid' => 100, 'csrfOk' => true, 'aclOk' => true];
    }

    /**
     * @param array<string, mixed> $o
     * @return array{docType: mixed, name: mixed, bytes: mixed}
     */
    protected function upload(array $o = []): array
    {
        return $o + ['docType' => 'lab_pdf', 'name' => '../../evil name.pdf', 'bytes' => self::PDF];
    }
}
