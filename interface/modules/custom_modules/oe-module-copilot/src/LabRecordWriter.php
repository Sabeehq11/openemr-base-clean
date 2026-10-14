<?php

/**
 * Writes supported lab rows from a validated agent extraction into OpenEMR's native lab tables
 * (procedure_order -> procedure_report -> procedure_result, linked to the source document by document_id).
 *
 * Rules: the agent stays read-only; this runs in the logged-in user's session after the ACL check in upload.php.
 * Values come only from the agent's response and are re-checked here. Insert-only, one transaction, and a row already
 * written for the same document is never written again or changed.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final class LabRecordWriter
{
    private const FLAGS = ['normal' => '', 'high' => 'high', 'low' => 'low', 'critical' => 'high', 'unknown' => ''];

    public function __construct(private readonly LabRecordStore $store, private readonly \Closure $clock)
    {
    }

    /**
     * @param array<int, mixed> $rows the agent's lab rows, in order; the array index is the row index reported back
     * @return array{links: array<int, string>, written: int, skipped: int}
     *         links maps row index => result uuid (new or already present); skipped counts rows not written
     */
    public function write(int $pid, int $documentId, array $rows, ?string $collectionDate): array
    {
        if ($pid <= 0 || $documentId <= 0) {
            throw new \InvalidArgumentException('invalid_target');
        }
        $when = $this->when($collectionDate);
        $valid = [];
        foreach ($rows as $index => $row) {
            $clean = is_array($row) ? $this->clean($row) : null;
            if ($clean !== null) {
                $valid[(int) $index] = $clean;
            }
        }
        $skipped = count($rows) - count($valid);
        if ($valid === []) {
            return ['links' => [], 'written' => 0, 'skipped' => $skipped];
        }

        $this->store->begin();
        try {
            $existing = $this->store->resultsForDocument($documentId);
            $links = [];
            $fresh = [];
            foreach ($valid as $index => $row) {
                if (isset($existing[$row['name']])) {
                    $links[$index] = $existing[$row['name']];   // retry or re-upload: reuse, never duplicate
                } else {
                    $fresh[$index] = $row;
                }
            }
            if ($fresh !== []) {
                $orderId = $this->store->insertOrder($pid, $when);
                $this->store->insertOrderCode($orderId, 'Uploaded lab report');
                $reportId = $this->store->insertReport($orderId, $when);
                foreach ($fresh as $index => $row) {
                    $links[$index] = $this->store->insertResult($reportId, $row, $documentId, $when);
                }
            }
            $this->store->commit();
        } catch (\Throwable $e) {
            $this->store->rollback();
            throw new \RuntimeException('lab_write_failed', 0, $e);
        }
        ksort($links);
        return ['links' => $links, 'written' => count($fresh), 'skipped' => $skipped];
    }

    /**
     * @param array<mixed> $row
     * @return array{name: string, value: string, unit: ?string, range: ?string, flag: string, loinc: ?string}|null
     */
    private function clean(array $row): ?array
    {
        if (($row['support'] ?? null) !== 'supported') {
            return null;
        }
        $name = $row['test_name'] ?? null;
        $value = $row['value'] ?? null;
        if (!is_string($name) || trim($name) === '' || mb_strlen($name) > 255) {
            return null;
        }
        if (!is_string($value) || preg_match('/^-?\d+(\.\d+)?$/', trim($value)) !== 1) {
            return null;
        }
        $flag = $row['abnormal_flag'] ?? 'unknown';
        $loinc = $row['loinc'] ?? null;
        return [
            'name' => trim($name),
            'value' => trim($value),
            'unit' => is_string($row['unit'] ?? null) ? mb_substr($row['unit'], 0, 31) : null,
            'range' => is_string($row['reference_range'] ?? null) ? mb_substr($row['reference_range'], 0, 255) : null,
            'flag' => is_string($flag) ? (self::FLAGS[$flag] ?? '') : '',
            'loinc' => is_string($loinc) && preg_match('/^\d{1,7}-\d$/', $loinc) === 1 ? $loinc : null,
        ];
    }

    private function when(?string $collectionDate): string
    {
        if ($collectionDate !== null && preg_match('/^\d{4}-\d{2}-\d{2}$/', $collectionDate) === 1) {
            $d = \DateTimeImmutable::createFromFormat('!Y-m-d', $collectionDate);
            if ($d !== false && $d->format('Y-m-d') === $collectionDate) {
                return $collectionDate . ' 00:00:00';
            }
        }
        return date('Y-m-d H:i:s', ($this->clock)());
    }
}
