<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

use OpenEMR\Modules\Copilot\LabRecordStore;
use OpenEMR\Modules\Copilot\LabRecordWriter;
use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/LabRecordStore.php';
require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/LabRecordWriter.php';

final class FakeLabStore implements LabRecordStore
{
    /** @var list<string> */
    public array $log = [];
    /** @var array<int, array<string, string>> document id => result_text => uuid */
    public array $results = [];
    public bool $failOnSecondResult = false;
    private int $next = 1;

    public function begin(): void
    {
        $this->log[] = 'begin';
    }

    public function commit(): void
    {
        $this->log[] = 'commit';
    }

    public function rollback(): void
    {
        $this->log[] = 'rollback';
    }

    public function resultsForDocument(int $documentId): array
    {
        return $this->results[$documentId] ?? [];
    }

    public function insertOrder(int $pid, string $when): int
    {
        $this->log[] = "order:$pid:$when";
        return 10;
    }

    public function insertOrderCode(int $orderId, string $name): void
    {
        $this->log[] = 'code';
    }

    public function insertReport(int $orderId, string $when): int
    {
        $this->log[] = 'report';
        return 20;
    }

    public function insertResult(int $reportId, array $row, int $documentId, string $when): string
    {
        if ($this->failOnSecondResult && $this->next > 1) {
            throw new \RuntimeException('db down');
        }
        $uuid = sprintf('00000000-0000-0000-0000-%012d', $this->next++);
        $this->log[] = 'result:' . $row['name'] . ':' . $row['value'] . ':' . $row['flag'];
        $this->results[$documentId][$row['name']] = $uuid;
        return $uuid;
    }
}

final class LabRecordWriterIsolatedTest extends TestCase
{
    private function writer(FakeLabStore $s): LabRecordWriter
    {
        return new LabRecordWriter($s, fn (): int => 1800000000);
    }

    /** @return array<string, mixed> */
    private function row(string $name, string $value, string $support = 'supported', string $flag = 'high'): array
    {
        return [
            'test_name' => $name, 'value' => $value, 'unit' => '%', 'reference_range' => '4-5',
            'abnormal_flag' => $flag, 'loinc' => '4548-4', 'support' => $support,
        ];
    }

    /** @return int number of log lines starting with the prefix */
    private function logCount(FakeLabStore $s, string $prefix): int
    {
        return count(array_filter($s->log, fn (string $l): bool => str_starts_with($l, $prefix)));
    }

    public function testWritesOneReportWithNResultsLinkedToDocument(): void
    {
        $s = new FakeLabStore();
        $out = $this->writer($s)->write(100, 7, [$this->row('A1c', '7.9'), $this->row('Creatinine', '1.8')], '2026-09-12');
        self::assertSame(2, $out['written']);
        self::assertCount(2, $out['links']);
        self::assertSame(1, $this->logCount($s, 'order:'));
        self::assertSame(1, $this->logCount($s, 'report'));
        self::assertContains('order:100:2026-09-12 00:00:00', $s->log);
        self::assertSame('begin', $s->log[0]);
        self::assertSame('commit', end($s->log));
    }

    public function testUnsupportedAndNonNumericRowsAreNeverWritten(): void
    {
        $s = new FakeLabStore();
        $out = $this->writer($s)->write(100, 7, [
            $this->row('A1c', '7.9', 'unsupported'), $this->row('Result', 'Positive'), $this->row('', '1'), $this->row('K', '4.1'),
        ], null);
        self::assertSame(1, $out['written']);
        self::assertSame(3, $out['skipped']);
        self::assertSame([3], array_keys($out['links']));
    }

    public function testRetryOrReuploadWritesNothingNew(): void
    {
        $s = new FakeLabStore();
        $w = $this->writer($s);
        $first = $w->write(100, 7, [$this->row('A1c', '7.9')], null);
        $before = count($s->log);
        $again = $w->write(100, 7, [$this->row('A1c', '7.9')], null);
        self::assertSame(0, $again['written']);
        self::assertSame($first['links'], $again['links']);
        $added = array_slice($s->log, $before);
        self::assertSame([], array_filter($added, fn (string $l): bool => str_starts_with($l, 'result:') || $l === 'report'));
    }

    public function testMidWriteFailureRollsBackAndSurfacesGenericError(): void
    {
        $s = new FakeLabStore();
        $s->failOnSecondResult = true;
        try {
            $this->writer($s)->write(100, 7, [$this->row('A1c', '7.9'), $this->row('K', '4.1')], null);
            self::fail('expected failure');
        } catch (\RuntimeException $e) {
            self::assertSame('lab_write_failed', $e->getMessage());
            self::assertStringNotContainsString('db down', $e->getMessage());
        }
        self::assertContains('rollback', $s->log);
        self::assertNotContains('commit', $s->log);
    }

    public function testNoValidRowsOpensNoTransaction(): void
    {
        $s = new FakeLabStore();
        $out = $this->writer($s)->write(100, 7, [$this->row('A1c', 'abc')], null);
        self::assertSame([], $s->log);
        self::assertSame(0, $out['written']);
    }

    public function testFlagMappingNeverTrustsFreeText(): void
    {
        $s = new FakeLabStore();
        $this->writer($s)->write(100, 7, [$this->row('A', '1', 'supported', 'weird'), $this->row('B', '2', 'supported', 'low')], null);
        self::assertContains('result:A:1:', $s->log);
        self::assertContains('result:B:2:low', $s->log);
    }

    public function testRejectsInvalidTargets(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->writer(new FakeLabStore())->write(0, 7, [], null);
    }

    public function testStoreInterfaceOffersNoUpdateOrDelete(): void
    {
        $methods = array_map(fn (\ReflectionMethod $m): string => strtolower($m->getName()), (new \ReflectionClass(LabRecordStore::class))->getMethods());
        foreach ($methods as $name) {
            self::assertDoesNotMatchRegularExpression('/update|delete|remove|replace/', $name);
        }
    }
}
