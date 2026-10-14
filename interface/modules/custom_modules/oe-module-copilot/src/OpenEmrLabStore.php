<?php

/**
 * Production LabRecordStore over OpenEMR's QueryUtils. Insert-only; mirrors the column choices proven in the C1 spike
 * (history-only complete order, report review_status='received', result.document_id = documents.id).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Uuid\UuidRegistry;

final class OpenEmrLabStore implements LabRecordStore
{
    public function begin(): void
    {
        QueryUtils::startTransaction();
    }

    public function commit(): void
    {
        QueryUtils::commitTransaction();
    }

    public function rollback(): void
    {
        QueryUtils::rollbackTransaction();
    }

    public function resultsForDocument(int $documentId): array
    {
        $out = [];
        $rows = QueryUtils::fetchRecords(
            'SELECT result_text, uuid FROM procedure_result WHERE document_id = ?',
            [$documentId]
        );
        foreach ($rows as $r) {
            if (is_string($r['uuid'] ?? null) && strlen($r['uuid']) === 16) {
                $out[(string) $r['result_text']] = UuidRegistry::uuidToString($r['uuid']);
            }
        }
        return $out;
    }

    public function insertOrder(int $pid, string $when): int
    {
        $uuid = (new UuidRegistry(['table_name' => 'procedure_order']))->createUuid();
        return (int) QueryUtils::sqlInsert(
            'INSERT INTO procedure_order (uuid, provider_id, patient_id, date_ordered, date_collected, order_status, '
            . "procedure_order_type, history_order, activity) VALUES (?, 0, ?, ?, ?, 'complete', 'laboratory_test', 1, 1)",
            [$uuid, $pid, $when, $when]
        );
    }

    public function insertOrderCode(int $orderId, string $name): void
    {
        QueryUtils::sqlInsert(
            'INSERT INTO procedure_order_code (procedure_order_id, procedure_order_seq, procedure_code, procedure_name) '
            . "VALUES (?, 1, 'UPLOAD', ?)",
            [$orderId, $name]
        );
    }

    public function insertReport(int $orderId, string $when): int
    {
        $uuid = (new UuidRegistry(['table_name' => 'procedure_report']))->createUuid();
        return (int) QueryUtils::sqlInsert(
            'INSERT INTO procedure_report (uuid, procedure_order_id, procedure_order_seq, date_collected, date_report, '
            . "report_status, review_status) VALUES (?, ?, 1, ?, ?, 'final', 'received')",
            [$uuid, $orderId, $when, $when]
        );
    }

    public function insertResult(int $reportId, array $row, int $documentId, string $when): string
    {
        $uuid = (new UuidRegistry(['table_name' => 'procedure_result']))->createUuid();
        QueryUtils::sqlInsert(
            'INSERT INTO procedure_result (uuid, procedure_report_id, result_data_type, result_code, result_text, date, '
            . "units, result, `range`, abnormal, result_status, document_id) VALUES (?, ?, 'S', ?, ?, ?, ?, ?, ?, ?, 'final', ?)",
            [
                $uuid, $reportId, $row['loinc'] ?? '', $row['name'], $when, $row['unit'] ?? '', $row['value'],
                $row['range'] ?? '', $row['flag'], $documentId,
            ]
        );
        return UuidRegistry::uuidToString($uuid);
    }
}
