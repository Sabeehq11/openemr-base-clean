<?php

/**
 * In-memory LabRecordStore for UploadProxy tests: records what was written, never touches a database.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\Copilot;

use OpenEMR\Modules\Copilot\LabRecordStore;

require_once __DIR__ . '/../../../../../interface/modules/custom_modules/oe-module-copilot/src/LabRecordStore.php';

final class UploadStore implements LabRecordStore
{
    /** @var list<string> */
    public array $log = [];
    private int $n = 1;

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
        return [];
    }

    public function insertOrder(int $pid, string $when): int
    {
        return 1;
    }

    public function insertOrderCode(int $orderId, string $name): void
    {
    }

    public function insertReport(int $orderId, string $when): int
    {
        return 1;
    }

    public function insertResult(int $reportId, array $row, int $documentId, string $when): string
    {
        $this->log[] = 'result:' . $row['name'];
        return sprintf('00000000-0000-0000-0000-%012d', $this->n++);
    }
}
