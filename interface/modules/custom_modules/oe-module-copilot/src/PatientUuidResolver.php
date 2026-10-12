<?php

/**
 * Resolves the chart's numeric pid (from the server-side session, never from the browser) to the patient UUID the agent uses.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final readonly class PatientUuidResolver
{
    /**
     * @param \Closure(int): (string|null) $lookup returns the 16-byte binary uuid stored in patient_data.uuid, or null
     */
    public function __construct(private \Closure $lookup)
    {
    }

    /**
     * @return string|null canonical lower-case UUID, or null when the pid is invalid or unknown
     */
    public function resolve(mixed $pid): ?string
    {
        if (is_string($pid) && ctype_digit($pid)) {
            $pid = (int) $pid;
        }
        if (!is_int($pid) || $pid <= 0) {
            return null;
        }
        $bytes = ($this->lookup)($pid);
        if (!is_string($bytes) || strlen($bytes) !== 16) {
            return null;
        }
        $hex = bin2hex($bytes);
        return sprintf(
            '%s-%s-%s-%s-%s',
            substr($hex, 0, 8),
            substr($hex, 8, 4),
            substr($hex, 12, 4),
            substr($hex, 16, 4),
            substr($hex, 20, 12),
        );
    }
}
