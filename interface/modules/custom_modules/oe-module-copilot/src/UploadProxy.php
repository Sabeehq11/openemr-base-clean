<?php

/**
 * Browser -> OpenEMR document store -> agent extraction -> chart lab records.
 *
 * The browser sends only a file and a document type. User, open patient, CSRF and ACL come from the server session.
 * The agent stays read-only: this class, running in the user's session, stores the document, asks the agent to
 * extract, writes supported lab rows through LabRecordWriter and reports the written record uuids back to the agent.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final class UploadProxy
{
    public const MAX_BYTES = 8 * 1024 * 1024;
    public const DOC_TYPES = ['lab_pdf', 'intake_form'];
    /** OpenEMR document categories: "Lab Report" and "Patient Information". */
    public const CATEGORY = ['lab_pdf' => 2, 'intake_form' => 4];

    /**
     * @param \Closure(string): string $tokenFor
     * @param \Closure(int, string): (int|null) $findDocument (pid, sha3-512 hash) => id of a live document, or null
     * @param \Closure(int, int, string, string, string): int $createDocument (pid, category, name, mime, bytes) => documents.id; throws on failure
     * @param \Closure(int): void $deleteDocument (documents.id) marks a document this request created as deleted, the way OpenEMR's own delete does
     */
    public function __construct(
        private readonly PatientUuidResolver $patients,
        private readonly LabRecordWriter $writer,
        private readonly AgentClient $agent,
        private readonly \Closure $tokenFor,
        private readonly \Closure $findDocument,
        private readonly \Closure $createDocument,
        private readonly \Closure $deleteDocument,
        private readonly int $maxBytes = self::MAX_BYTES,
    ) {
    }

    /**
     * @param array{userUuid: ?string, pid: mixed, csrfOk: bool, aclOk: bool} $session facts from the server session
     * @param array{docType: mixed, name: mixed, bytes: mixed} $upload what the browser sent
     * @return array{status: int, error?: string, body?: array<string, mixed>}
     */
    public function handle(array $session, array $upload, string $cid): array
    {
        if (!$session['csrfOk']) {
            return ['status' => 403, 'error' => 'csrf'];
        }
        $user = $session['userUuid'];
        if (!is_string($user) || $user === '') {
            return ['status' => 401, 'error' => 'not_logged_in'];
        }
        if (!$session['aclOk']) {
            return ['status' => 403, 'error' => 'forbidden'];
        }
        $pid = $session['pid'];
        if (is_string($pid) && ctype_digit($pid)) {
            $pid = (int) $pid;
        }
        $patient = is_int($pid) ? $this->patients->resolve($pid) : null;
        if (!is_int($pid) || $patient === null) {
            return ['status' => 409, 'error' => 'no_patient_open'];
        }
        $docType = $upload['docType'];
        $bytes = $upload['bytes'];
        if (!is_string($docType) || !in_array($docType, self::DOC_TYPES, true)) {
            return ['status' => 400, 'error' => 'bad_doc_type'];
        }
        if (!is_string($bytes) || $bytes === '') {
            return ['status' => 400, 'error' => 'empty_file'];
        }
        if (strlen($bytes) > $this->maxBytes) {
            return ['status' => 413, 'error' => 'too_large'];
        }
        $mime = self::sniff($bytes);
        if ($mime === null) {
            return ['status' => 415, 'error' => 'unsupported_type'];
        }

        try {
            $token = ($this->tokenFor)($user);
        } catch (\Throwable) {
            return ['status' => 503, 'error' => 'token_unavailable'];
        }

        // Authorize and validate BEFORE anything is written to OpenEMR. The agent applies the same care-team check it
        // applies to every read, so a patient the user is not on the care team of never gets a document filed.
        $pre = $this->agent->post('/v1/w2/preflight', [
            'patient_uuid' => $patient,
            'doc_type' => $docType,
            'content_b64' => base64_encode($bytes),
        ], $user, $token, $cid);
        if ($pre === null) {
            return ['status' => 503, 'error' => 'agent_unreachable'];
        }
        if ($pre[0] !== 200) {
            return $this->refusal($pre);
        }

        // Same bytes for the same patient reuse the stored document, so OpenEMR gets no duplicate.
        $documentId = ($this->findDocument)($pid, hash('sha3-512', $bytes));
        $created = false;
        if ($documentId === null) {
            try {
                $name = self::safeName($upload['name'], $mime);
                $documentId = ($this->createDocument)($pid, self::CATEGORY[$docType], $name, $mime, $bytes);
                $created = true;
            } catch (\Throwable) {
                return ['status' => 500, 'error' => 'store_failed'];
            }
        }

        $reply = $this->agent->post('/v1/w2/documents', [
            'patient_uuid' => $patient,
            'document_id' => $documentId,
            'doc_type' => $docType,
            'content_b64' => base64_encode($bytes),
        ], $user, $token, $cid);
        if ($reply === null || $reply[0] !== 200 || !is_array($reply[1]['document'] ?? null)) {
            // The upload was refused after the file was filed: take back the document this request created. A document
            // that already existed (a re-upload) is never touched.
            if ($created) {
                $this->discard($documentId);
            }
            return $reply === null ? ['status' => 503, 'error' => 'agent_unreachable'] : $this->refusal($reply);
        }
        [, $data] = $reply;
        $document = $data['document'];

        $chart = ['written' => 0, 'skipped' => 0, 'state' => 'not_applicable'];
        $guideline = null;
        if ($docType === 'lab_pdf' && ($document['status'] ?? null) === 'extracted') {
            $rows = is_array($document['lab']['rows'] ?? null) ? array_values($document['lab']['rows']) : [];
            $date = $document['lab']['collection_date'] ?? null;
            $date = is_string($date) ? $date : null;
            $chart = $this->writeChart($pid, $patient, $documentId, $rows, $date, [$user, $token, $cid]);
            $guideline = $this->guideline($patient, $rows, [$user, $token, $cid]);
        }

        unset($document['patient_uuid'], $document['sha256']);
        return ['status' => 200, 'body' => [
            'document' => $document,
            'duplicate' => ($data['duplicate'] ?? false) === true,
            'chart' => $chart,
            'guideline' => $guideline,
        ]];
    }

    /**
     * Maps an agent refusal to the user-facing status. A care-team denial is always 403.
     *
     * @param array{0: int, 1: array<mixed>} $reply
     * @return array{status: int, error: string}
     */
    private function refusal(array $reply): array
    {
        [$status, $data] = $reply;
        $code = is_string($data['error'] ?? null) ? $data['error'] : 'agent_error';
        if ($status === 403) {
            return ['status' => 403, 'error' => 'forbidden'];
        }
        return ['status' => in_array($status, [400, 413, 415], true) ? $status : 502, 'error' => $code];
    }

    private function discard(int $documentId): void
    {
        try {
            ($this->deleteDocument)($documentId);
        } catch (\Throwable) {
            // Best effort: the refusal is still reported. A leftover document is visible to staff in the chart.
        }
    }

    /**
     * @param list<mixed> $rows
     * @param array{string, string, string} $auth user, token, correlation id
     * @return array{written: int, skipped: int, state: string}
     */
    private function writeChart(int $pid, string $patient, int $documentId, array $rows, ?string $date, array $auth): array
    {
        try {
            $res = $this->writer->write($pid, $documentId, $rows, $date);
        } catch (\Throwable) {
            // The writer rolled back, so no partial report exists. The document and the extraction remain.
            return ['written' => 0, 'skipped' => count($rows), 'state' => 'failed'];
        }
        if ($res['links'] !== []) {
            $links = [];
            foreach ($res['links'] as $index => $uuid) {
                $links[(string) $index] = $uuid;
            }
            $this->agent->post(
                '/v1/w2/documents/' . $documentId . '/links',
                // An object, not a list: PHP encodes keys "0", "1", ... as a JSON array, which the agent rejects.
                ['patient_uuid' => $patient, 'links' => (object) $links],
                ...$auth,
            );
        }
        return ['written' => $res['written'], 'skipped' => $res['skipped'], 'state' => 'ok'];
    }

    /**
     * Guideline lookup from the names of abnormal tests only: no value, date or identifier is added to the query.
     *
     * @param list<mixed> $rows
     * @param array{string, string, string} $auth user, token, correlation id
     * @return array<mixed>|null
     */
    private function guideline(string $patient, array $rows, array $auth): ?array
    {
        $names = [];
        foreach ($rows as $row) {
            if (
                is_array($row) && ($row['support'] ?? null) === 'supported'
                && !in_array($row['abnormal_flag'] ?? 'unknown', ['normal', 'unknown'], true)
                && is_string($row['test_name'] ?? null)
            ) {
                $names[] = mb_substr($row['test_name'], 0, 60);
            }
        }
        if ($names === []) {
            return null;
        }
        $reply = $this->agent->post('/v1/w2/retrieve', [
            'patient_uuid' => $patient,
            'question' => 'Guideline recommendations for abnormal results',
            'test_names' => array_slice(array_values(array_unique($names)), 0, 10),
        ], ...$auth);
        return ($reply !== null && $reply[0] === 200) ? $reply[1] : null;
    }

    /** File kind by content, never by the browser's declared type or file name. */
    public static function sniff(string $bytes): ?string
    {
        return match (true) {
            str_starts_with($bytes, '%PDF-') => 'application/pdf',
            str_starts_with($bytes, "\x89PNG\r\n\x1a\n") => 'image/png',
            str_starts_with($bytes, "\xff\xd8\xff") => 'image/jpeg',
            default => null,
        };
    }

    private static function safeName(mixed $name, string $mime): string
    {
        $ext = ['application/pdf' => 'pdf', 'image/png' => 'png', 'image/jpeg' => 'jpg'][$mime];
        $base = is_string($name) ? pathinfo($name, PATHINFO_FILENAME) : '';
        $base = trim((string) preg_replace('/[^A-Za-z0-9._-]+/', '_', $base), '._');
        return ($base === '' ? 'upload' : mb_substr($base, 0, 80)) . '.' . $ext;
    }
}
