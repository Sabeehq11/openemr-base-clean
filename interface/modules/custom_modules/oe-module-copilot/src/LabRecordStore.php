<?php

/**
 * Storage seam for LabRecordWriter. Insert-only by design: there is no update or delete method, so the writer cannot
 * overwrite or remove an existing chart value even by mistake.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

interface LabRecordStore
{
    public function begin(): void;

    public function commit(): void;

    public function rollback(): void;

    /**
     * Results already linked to this source document, keyed by result_text.
     *
     * @return array<string, string> result_text => result uuid (canonical string)
     */
    public function resultsForDocument(int $documentId): array;

    /** History-only, complete order for the patient. Returns the new procedure_order_id. */
    public function insertOrder(int $pid, string $when): int;

    public function insertOrderCode(int $orderId, string $name): void;

    /** Unreviewed report ("received") for the order. Returns the new procedure_report_id. */
    public function insertReport(int $orderId, string $when): int;

    /**
     * @param array{name: string, value: string, unit: ?string, range: ?string, flag: string, loinc: ?string} $row
     * @return string the new result uuid (canonical string)
     */
    public function insertResult(int $reportId, array $row, int $documentId, string $when): string;
}
