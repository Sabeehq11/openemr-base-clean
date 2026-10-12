<?php

/**
 * HMAC signing of module -> agent requests. Mirrors agent/app/signing.py exactly.
 *
 * The signature is an integrity check only. It never grants access: the agent re-checks the user's token and the
 * patient-level decision on every call.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final readonly class RequestSigner
{
    private const VERSION = 'v1';

    public function __construct(private string $secret)
    {
        if ($secret === '') {
            throw new \InvalidArgumentException('Signing secret must not be empty');
        }
    }

    public function canonical(
        string $method,
        string $path,
        string $timestamp,
        string $nonce,
        string $body,
        string $userUuid,
        string $token,
    ): string {
        return implode("\n", [
            self::VERSION,
            strtoupper($method),
            $path,
            $timestamp,
            $nonce,
            hash('sha256', $body),
            $userUuid,
            hash('sha256', $token),
        ]);
    }

    public function sign(
        string $method,
        string $path,
        string $timestamp,
        string $nonce,
        string $body,
        string $userUuid,
        string $token,
    ): string {
        return hash_hmac('sha256', $this->canonical($method, $path, $timestamp, $nonce, $body, $userUuid, $token), $this->secret);
    }

    /**
     * @return array<string, string> headers the agent gateway requires
     */
    public function headers(string $method, string $path, string $body, string $userUuid, string $token, int $now, string $nonce): array
    {
        $ts = (string) $now;
        return [
            'x-copilot-timestamp' => $ts,
            'x-copilot-nonce' => $nonce,
            'x-copilot-signature' => $this->sign($method, $path, $ts, $nonce, $body, $userUuid, $token),
            'x-copilot-user' => $userUuid,
            'x-copilot-token' => $token,
            'content-type' => 'application/json',
        ];
    }
}
