<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

require_once __DIR__ . '/UploadProxyTestCase.php';

final class UploadProxyIsolatedTest extends UploadProxyTestCase
{
    public function testHappyPathStoresSignsWritesChartAndReportsLinks(): void
    {
        $r = $this->proxy()->handle($this->session(), $this->upload(), 'cid-1');
        $this->assertSame(200, $r['status']);
        $this->assertSame(['100:2:evil_name.pdf:application/pdf'], $this->created);
        $results = array_values(array_filter($this->store->log, fn ($l) => str_starts_with($l, 'result')));
        $this->assertSame(['result:Hemoglobin A1c', 'result:Sodium'], $results);
        $this->assertSame(['written' => 2, 'skipped' => 1, 'state' => 'ok'], $r['body']['chart']);
        $paths = array_map(fn ($s) => (string) parse_url($s['url'], PHP_URL_PATH), $this->sent);
        $this->assertSame(['/v1/w2/preflight', '/v1/w2/documents', '/v1/w2/documents/5/links', '/v1/w2/retrieve'], $paths);
        $this->assertSame(self::PATIENT, $this->sent[0]['body']['patient_uuid']);
        $this->assertCount(2, $this->sent[2]['body']['links']);
        // The agent needs a JSON object keyed by row index; a sequential PHP array would be sent as a list (HTTP 400).
        $this->assertStringContainsString('"links":{"', $this->sent[2]['raw']);
        $this->assertArrayHasKey('x-copilot-signature', $this->sent[0]['headers']);
        $this->assertArrayNotHasKey('sha256', $r['body']['document']);
    }

    public function testRefusalsHappenBeforeAnythingIsStoredOrSent(): void
    {
        $big = '%PDF-' . str_repeat('a', 1000);
        $cases = [
            'csrf' => [$this->session(['csrfOk' => false]), $this->upload(), 403],
            'not logged in' => [$this->session(['userUuid' => null]), $this->upload(), 401],
            'acl' => [$this->session(['aclOk' => false]), $this->upload(), 403],
            'no patient' => [$this->session(['pid' => null]), $this->upload(), 409],
            'unknown patient' => [$this->session(['pid' => 7]), $this->upload(), 409],
            'bad type' => [$this->session(), $this->upload(['docType' => 'xray']), 400],
            'empty' => [$this->session(), $this->upload(['bytes' => '']), 400],
            'too large' => [$this->session(), $this->upload(['bytes' => $big]), 413],
            'wrong content' => [$this->session(), $this->upload(['bytes' => 'MZ not a pdf']), 415],
        ];
        foreach ($cases as $label => [$session, $upload, $expected]) {
            $r = $this->proxy()->handle($session, $upload, 'cid');
            $this->assertSame($expected, $r['status'], $label);
            $this->assertSame([], $this->created, $label);
            $this->assertSame([], $this->sent, $label);
        }
    }

    public function testBrowserCannotChoosePatientOrSendValuesToWrite(): void
    {
        $extra = ['patient_uuid' => 'ffffffff-ffff-ffff-ffff-ffffffffffff', 'rows' => [['test_name' => 'X']]];
        $r = $this->proxy()->handle($this->session(), $this->upload() + $extra, 'cid');
        $this->assertSame(200, $r['status']);
        $this->assertSame(self::PATIENT, $this->sent[0]['body']['patient_uuid']);
        $this->assertNotContains('result:X', $this->store->log);
    }

    public function testSniffingIgnoresTheDeclaredNameAndType(): void
    {
        $this->assertSame('application/pdf', \OpenEMR\Modules\Copilot\UploadProxy::sniff('%PDF-1.7'));
        $this->assertSame('image/png', \OpenEMR\Modules\Copilot\UploadProxy::sniff("\x89PNG\r\n\x1a\n..."));
        $this->assertNull(\OpenEMR\Modules\Copilot\UploadProxy::sniff('<?php echo 1;'));
    }
}
