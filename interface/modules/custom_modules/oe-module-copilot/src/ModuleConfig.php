<?php

/**
 * Module settings. Secrets live in a git-ignored config.local.php next to the module (see config.local.php.example),
 * never in the database dump, the repo, or any page sent to the browser.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\Copilot;

final readonly class ModuleConfig
{
    public function __construct(
        public string $agentBaseUrl,
        public string $signingSecret,
        public string $oauthClientId,
        public bool $w2Chat = false,
    ) {
    }

    /** @param array<mixed> $values */
    public static function fromArray(array $values): ?self
    {
        $url = $values['agent_base_url'] ?? null;
        $secret = $values['signing_secret'] ?? null;
        $client = $values['oauth_client_id'] ?? null;
        foreach ([$url, $secret, $client] as $v) {
            if (!is_string($v) || $v === '') {
                return null;
            }
        }
        if (!preg_match('#^https?://[A-Za-z0-9._\-]+(:\d+)?$#', rtrim($url, '/'))) {
            return null;
        }
        // Optional and off by default: route chat to the Week 2 supervisor path (/v1/w2/chat).
        return new self(rtrim($url, '/'), $secret, $client, ($values['w2_chat'] ?? false) === true);
    }

    public static function load(string $moduleDir): ?self
    {
        $file = $moduleDir . '/config.local.php';
        if (!is_file($file)) {
            return null;
        }
        $values = require $file;
        return is_array($values) ? self::fromArray($values) : null;
    }
}
