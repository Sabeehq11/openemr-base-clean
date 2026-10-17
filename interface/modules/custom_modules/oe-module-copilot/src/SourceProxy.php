<?php

/**
 * Click-to-source: one page of a stored document, for the citation viewer.
 *
 * The browser sends only a document id and a page number. The user, the open patient and the ACL come from the
 * server session. A document is shown only if it belongs to the open patient and is not deleted. Images are returned
 * as stored. A PDF page is rendered by the agent from the bytes this class read from OpenEMR; the agent checks the
 * bytes against the hash of the document it extracted, so a page is only ever shown for that document.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final class SourceProxy
{
    public const MAX_BYTES = 8 * 1024 * 1024;
    public const MAX_PAGE = 50;

    /**
     * @param \Closure(string): string $tokenFor
     * @param \Closure(int): (array{pid: int, deleted: bool}|null) $documentInfo documents.id => owner pid and deleted flag, or null when there is no such row
     * @param \Closure(int): (string|null) $documentBytes documents.id => stored file content, or null when it cannot be read
     */
    public function __construct(
        private readonly PatientUuidResolver $patients,
        private readonly AgentClient $agent,
        private readonly \Closure $tokenFor,
        private readonly \Closure $documentInfo,
        private readonly \Closure $documentBytes,
        private readonly int $maxBytes = self::MAX_BYTES,
    ) {
    }

    /**
     * @param array{userUuid: ?string, pid: mixed, aclOk: bool} $session facts from the server session
     * @param array{documentId: mixed, page: mixed} $request what the browser sent
     * @return array{status: int, error?: string, mime?: string, bytes?: string}
     */
    public function handle(array $session, array $request, string $cid): array
    {
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
        $documentId = self::positiveInt($request['documentId']);
        $page = self::positiveInt($request['page']);
        if ($documentId === null || $page === null || $page > self::MAX_PAGE) {
            return ['status' => 400, 'error' => 'bad_request'];
        }

        // Ownership first, before any bytes are read. A document of another patient, a deleted one and a missing one
        // all look the same to the caller.
        $info = ($this->documentInfo)($documentId);
        if ($info === null || $info['deleted'] || $info['pid'] !== $pid) {
            self::logStep($cid, 'not_found');
            return ['status' => 404, 'error' => 'not_found'];
        }
        $bytes = ($this->documentBytes)($documentId);
        if (!is_string($bytes) || $bytes === '' || strlen($bytes) > $this->maxBytes) {
            self::logStep($cid, 'unreadable');
            return ['status' => 404, 'error' => 'not_found'];
        }
        $mime = UploadProxy::sniff($bytes);
        if ($mime === null) {
            self::logStep($cid, 'unsupported');
            return ['status' => 415, 'error' => 'unsupported_type'];
        }
        if ($mime !== 'application/pdf') {
            if ($page !== 1) {
                return ['status' => 404, 'error' => 'not_found'];
            }
            self::logStep($cid, 'image');
            return ['status' => 200, 'mime' => $mime, 'bytes' => $bytes];
        }

        try {
            $token = ($this->tokenFor)($user);
        } catch (\Throwable) {
            return ['status' => 503, 'error' => 'token_unavailable'];
        }
        $reply = $this->agent->post('/v1/w2/documents/' . $documentId . '/page', [
            'patient_uuid' => $patient,
            'page' => $page,
            'content_b64' => base64_encode($bytes),
        ], $user, $token, $cid);
        if ($reply === null) {
            self::logStep($cid, 'agent_unreachable');
            return ['status' => 503, 'error' => 'agent_unreachable'];
        }
        [$status, $data] = $reply;
        if ($status === 403) {
            return ['status' => 403, 'error' => 'forbidden'];
        }
        if ($status === 404) {
            // The agent's own "no such document or page" always carries error=not_found. Any other 404 (the framework's
            // `{"detail":"Not Found"}`) means the agent has no such route: it is running an older build than this module.
            // That is a deployment fault, not a missing page, so it is reported as one instead of looking like a bad id.
            if (($data['error'] ?? null) !== 'not_found') {
                self::logStep($cid, 'agent_route_missing');
                return ['status' => 502, 'error' => 'agent_outdated'];
            }
            self::logStep($cid, 'not_found');
            return ['status' => 404, 'error' => 'not_found'];
        }
        $image = ($status === 200 && is_string($data['content_b64'] ?? null))
            ? base64_decode($data['content_b64'], true) : false;
        $imageMime = is_string($image) ? UploadProxy::sniff($image) : null;
        if (!is_string($image) || $imageMime === null || $imageMime === 'application/pdf') {
            // Anything else the agent said (including a hash mismatch) is a generic failure, never its message.
            self::logStep($cid, 'agent_error');
            return ['status' => 502, 'error' => 'agent_error'];
        }
        self::logStep($cid, 'pdf_page');
        return ['status' => 200, 'mime' => $imageMime, 'bytes' => $image];
    }

    private static function positiveInt(mixed $value): ?int
    {
        if (is_string($value) && ctype_digit($value) && strlen($value) <= 9) {
            $value = (int) $value;
        }
        return is_int($value) && $value > 0 ? $value : null;
    }

    /** One JSON line, so a click can be followed by correlation id. Never a patient id, file name or content. */
    private static function logStep(string $cid, string $status): void
    {
        error_log((string) json_encode([
            'event' => 'w2_source',
            'correlation_id' => $cid,
            'status' => $status,
        ]));
    }
}
