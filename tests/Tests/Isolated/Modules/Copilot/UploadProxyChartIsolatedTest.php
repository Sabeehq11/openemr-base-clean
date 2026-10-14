<?php

/**
 * Chart-side behaviour of UploadProxy: reuse, agent failure, guideline query contents.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

require_once __DIR__ . '/UploadProxyTestCase.php';

final class UploadProxyChartIsolatedTest extends UploadProxyTestCase
{
    public function testGuidelineQueryHoldsOnlyAbnormalSupportedTestNames(): void
    {
        $this->proxy()->handle($this->session(), $this->upload(), 'cid');
        $q = $this->sent[3]['body'];
        $this->assertSame(['Hemoglobin A1c'], $q['test_names']);
        $this->assertStringNotContainsString('7.9', json_encode($q, JSON_THROW_ON_ERROR));
    }

    public function testReuploadReusesTheStoredDocumentAndCreatesNothing(): void
    {
        $r = $this->proxy(42)->handle($this->session(), $this->upload(), 'cid');
        $this->assertSame(200, $r['status']);
        $this->assertSame([], $this->created);
        $this->assertSame(42, $this->sent[1]['body']['document_id']);
    }

    public function testAgentRefusalIsMappedAndNothingIsWrittenToTheChart(): void
    {
        $r = $this->proxy(null, 415)->handle($this->session(), $this->upload(), 'cid');
        $this->assertSame(['status' => 415, 'error' => 'unsupported_type'], $r);
        $this->assertSame([], $this->store->log);
    }

    public function testCareTeamDenialIs403AndNothingIsStoredOrWritten(): void
    {
        // Guards the live finding: a denied patient must not get a document filed (it used to be stored first, 502).
        $proxy = $this->proxy();
        $this->preflightStatus = 403;
        $r = $proxy->handle($this->session(), $this->upload(), 'cid');
        $this->assertSame(['status' => 403, 'error' => 'forbidden'], $r);
        $this->assertSame([], $this->created);
        $this->assertSame([], $this->deleted);
        $this->assertSame([], $this->store->log);
        $paths = array_map(fn ($s) => (string) parse_url($s['url'], PHP_URL_PATH), $this->sent);
        $this->assertSame(['/v1/w2/preflight'], $paths); // no extraction request, no chart write
    }

    public function testPreflightRejectionOfABadFileStoresNothing(): void
    {
        $proxy = $this->proxy();
        $this->preflightStatus = 415;
        $r = $proxy->handle($this->session(), $this->upload(), 'cid');
        $this->assertSame(415, $r['status']);
        $this->assertSame([], $this->created);
        $this->assertSame([], $this->store->log);
    }

    public function testDocumentCreatedByThisRequestIsTakenBackWhenTheAgentRefusesAfterwards(): void
    {
        // Guards a rejected upload leaving a document behind if the agent refuses after the file was filed.
        $r = $this->proxy(null, 415)->handle($this->session(), $this->upload(), 'cid');
        $this->assertSame(415, $r['status']);
        $this->assertCount(1, $this->created);
        $this->assertSame([5], $this->deleted);
        $this->assertSame([], $this->store->log);
    }

    public function testAnExistingDocumentIsNeverDeletedWhenAReuploadIsRefused(): void
    {
        $r = $this->proxy(42, 415)->handle($this->session(), $this->upload(), 'cid');
        $this->assertSame(415, $r['status']);
        $this->assertSame([], $this->created);
        $this->assertSame([], $this->deleted);
    }

    public function testFailedExtractionKeepsTheDocumentAndWritesNoLabs(): void
    {
        $r = $this->proxy(null, 200, 'extraction_failed')->handle($this->session(), $this->upload(), 'cid');
        $this->assertSame(200, $r['status']);
        $this->assertSame([], $this->store->log);
        $this->assertSame('not_applicable', $r['body']['chart']['state']);
    }

    public function testIntakeFormIsFiledUnderPatientInformationAndNeverWritesLabs(): void
    {
        $r = $this->proxy()->handle($this->session(), $this->upload(['docType' => 'intake_form']), 'cid');
        $this->assertSame(200, $r['status']);
        $this->assertSame(['100:4:evil_name.pdf:application/pdf'], $this->created);
        $this->assertSame([], $this->store->log);
        $this->assertCount(2, $this->sent); // preflight, then extraction; no links, no guideline
    }
}
