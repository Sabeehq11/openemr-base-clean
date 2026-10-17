<?php

/**
 * Signed JSON calls from the module to the agent. The signature covers method, path, body hash, user and token hash.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final class AgentClient
{
    /**
     * @param \Closure(string, array<string,string>, string): array{0: int, 1: string} $http (url, headers, body) => [status, body]; status 0 on transport failure
     * @param \Closure(): string $nonce
     * @param \Closure(): int $clock
     */
    public function __construct(
        private readonly RequestSigner $signer,
        private readonly string $baseUrl,
        private readonly \Closure $http,
        private readonly \Closure $nonce,
        private readonly \Closure $clock,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @return array{0: int, 1: array<mixed>}|null null when the agent could not be reached
     */
    public function post(string $path, array $payload, string $user, string $token, string $correlationId): ?array
    {
        $body = json_encode($payload, JSON_THROW_ON_ERROR);
        $headers = $this->signer->headers('POST', $path, $body, $user, $token, ($this->clock)(), ($this->nonce)());
        $headers['x-correlation-id'] = $correlationId;
        $headers['content-type'] = 'application/json';
        [$status, $raw] = ($this->http)(rtrim($this->baseUrl, '/') . $path, $headers, $body);
        if ($status === 0) {
            return null;
        }
        $decoded = json_decode($raw, true);
        return [$status, is_array($decoded) ? $decoded : []];
    }
}
