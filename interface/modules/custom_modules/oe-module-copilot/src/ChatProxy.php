<?php

/**
 * Browser -> agent proxy. The browser sends only a question and a conversation id. Everything that carries authority
 * (who the user is, which patient is open, the OAuth token, the signature) is produced here from the server session.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final class ChatProxy
{
    public const MAX_QUESTION = 1000;
    public const PATH_W1 = '/v1/chat';
    public const PATH_W2 = '/v1/w2/chat';

    /**
     * @param \Closure(string): string $tokenFor issues a user token for the given user uuid
     * @param \Closure(string $url, array<string,string> $headers, string $body, callable(string): void $onChunk): int $post
     *        streams the agent response into $onChunk and returns the HTTP status (0 on transport failure)
     * @param \Closure(): string $nonce
     */
    public function __construct(
        private readonly RequestSigner $signer,
        private readonly PatientUuidResolver $patients,
        private readonly string $agentBaseUrl,
        private readonly \Closure $tokenFor,
        private readonly \Closure $post,
        private readonly \Closure $nonce,
        private readonly \Closure $clock,
        private readonly bool $w2Chat = false,
    ) {
    }

    /**
     * @param array{userUuid: ?string, pid: mixed, csrfOk: bool} $session facts read from the server-side session
     * @param array<mixed> $input decoded JSON body from the browser
     * @return array{status: int, error?: string}  status 200 means the stream was relayed through $emit
     */
    public function handle(array $session, array $input, string $correlationId, callable $emit): array
    {
        if (!$session['csrfOk']) {
            return ['status' => 403, 'error' => 'csrf'];
        }
        $user = $session['userUuid'];
        if (!is_string($user) || $user === '') {
            return ['status' => 401, 'error' => 'not_logged_in'];
        }
        $patient = $this->patients->resolve($session['pid']);
        if ($patient === null) {
            return ['status' => 409, 'error' => 'no_patient_open'];
        }
        $question = $input['question'] ?? null;
        if (!is_string($question) || $question === '' || mb_strlen($question) > self::MAX_QUESTION) {
            return ['status' => 400, 'error' => 'bad_question'];
        }
        $conversation = $input['conversation_id'] ?? null;
        $body = json_encode([
            'patient_uuid' => $patient,   // from the session, never from the browser
            'question' => $question,
            'conversation_id' => is_string($conversation) ? $conversation : null,
        ], JSON_THROW_ON_ERROR);

        try {
            $token = ($this->tokenFor)($user);
        } catch (\Throwable) {
            return ['status' => 503, 'error' => 'token_unavailable'];
        }
        // Default stays the Week 1 path. The browser never chooses it; only module config does.
        $path = $this->w2Chat ? self::PATH_W2 : self::PATH_W1;
        $headers = $this->signer->headers('POST', $path, $body, $user, $token, ($this->clock)(), ($this->nonce)());
        $headers['x-correlation-id'] = $correlationId;
        $status = ($this->post)(rtrim($this->agentBaseUrl, '/') . $path, $headers, $body, $emit);
        if ($status === 0) {
            return ['status' => 503, 'error' => 'agent_unreachable'];
        }
        if ($status !== 200) {
            return ['status' => 502, 'error' => 'agent_error'];
        }
        return ['status' => 200];
    }
}
