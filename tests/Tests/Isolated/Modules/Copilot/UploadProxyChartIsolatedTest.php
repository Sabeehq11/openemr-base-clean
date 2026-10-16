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

    /**
     * Runs the upload with PHP's error_log pointed at a temp file and returns the w2_openemr_write events it wrote.
     *
     * @return list<array<string, mixed>>
     */
    private function loggedSteps(string $cid): array
    {
        $file = tempnam(sys_get_temp_dir(), 'w2log');
        $this->assertIsString($file);
        $previous = ini_set('error_log', $file);
        try {
            $this->proxy()->handle($this->session(), $this->upload(), $cid);
        } finally {
            ini_set('error_log', $previous === false ? '' : $previous);
        }
        $raw = (string) file_get_contents($file);
        unlink($file);
        $events = [];
        foreach (explode("\n", $raw) as $line) {
            $start = strpos($line, '{"event"');
            if ($start !== false) {
                $events[] = json_decode(substr($line, $start), true, 512, JSON_THROW_ON_ERROR);
            }
        }
        return $events;
    }

    public function testWriteAndLinkStepsAreLoggedWithTheCorrelationIdAndCountsOnly(): void
    {
        // Guards a trace that stops at the agent: the OpenEMR write and the link back must carry the correlation id.
        $events = $this->loggedSteps('cid-link-1');
        $this->assertSame(['openemr_write', 'link'], array_column($events, 'step'));
        foreach ($events as $e) {
            $this->assertSame('w2_openemr_write', $e['event']);
            $this->assertSame('cid-link-1', $e['correlation_id']);
            $this->assertSame('ok', $e['status']);
            $this->assertSame(['event', 'correlation_id', 'step', 'status', 'rows'], array_keys($e));
            $this->assertIsInt($e['rows']);
        }
    }

    public function testFailedLinkIsLoggedFailedAndDoesNotChangeTheChartResult(): void
    {
        // Guards a silent link failure, and the logging line itself changing the upload outcome.
        $this->linkStatus = 502;
        $events = $this->loggedSteps('cid-link-2');
        $this->assertSame('failed', $events[1]['status']);
        $this->assertSame('link', $events[1]['step']);
        $this->assertSame('ok', $events[0]['status']);
    }
}
