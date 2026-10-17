<?php

/**
 * Co-Pilot chat proxy endpoint. POST JSON {question, conversation_id?}; streams the agent's SSE back.
 *
 * Authority comes only from the server session: user, open patient, CSRF. The browser cannot supply any of it.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

require_once __DIR__ . '/../../../../globals.php';
require_once __DIR__ . '/../src/RequestSigner.php';
require_once __DIR__ . '/../src/PatientUuidResolver.php';
require_once __DIR__ . '/../src/ChatProxy.php';
require_once __DIR__ . '/../src/ModuleConfig.php';
require_once __DIR__ . '/../src/UserTokenIssuer.php';

use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\Copilot\ChatProxy;
use OpenEMR\Modules\Copilot\ModuleConfig;
use OpenEMR\Modules\Copilot\PatientUuidResolver;
use OpenEMR\Modules\Copilot\RequestSigner;
use OpenEMR\Modules\Copilot\UserTokenIssuer;

function copilot_fail(int $status, string $error): never
{
    http_response_code($status);
    header('Content-Type: application/json');
    echo json_encode(['error' => $error]);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    copilot_fail(405, 'method_not_allowed');
}
$config = ModuleConfig::load(dirname(__DIR__));
if ($config === null) {
    copilot_fail(503, 'not_configured');
}

$session = SessionWrapperFactory::getInstance()->getActiveSession();
$input = json_decode((string) file_get_contents('php://input'), true);
if (!is_array($input)) {
    copilot_fail(400, 'bad_request');
}
$userId = (int) ($session->get('authUserID') ?? 0);
$userRow = $userId > 0 ? sqlQuery('SELECT uuid FROM users WHERE id = ?', [$userId]) : false;
$userUuid = (is_array($userRow) && is_string($userRow['uuid'] ?? null) && strlen($userRow['uuid']) === 16)
    ? UuidRegistry::uuidToString($userRow['uuid']) : null;

$facts = [
    'userUuid' => $userUuid,
    'pid' => $session->get('pid'),
    'csrfOk' => CsrfUtils::verifyCsrfToken($_SERVER['HTTP_X_CSRF_TOKEN'] ?? '', session: $session),
];

$signer = new RequestSigner($config->signingSecret);
$resolver = new PatientUuidResolver(function (int $pid): ?string {
    $row = sqlQuery('SELECT uuid FROM patient_data WHERE pid = ?', [$pid]);
    return is_array($row) && is_string($row['uuid'] ?? null) ? $row['uuid'] : null;
});
$siteDir = (string) ($GLOBALS['OE_SITE_DIR'] ?? '');
$issuer = new UserTokenIssuer($config->oauthClientId, $siteDir);

$post = function (string $url, array $headers, string $body, callable $onChunk): int {
    $status = 0;
    $h = [];
    foreach ($headers as $k => $v) {
        $h[] = $k . ': ' . $v;
    }
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_HTTPHEADER => $h,
        CURLOPT_CONNECTTIMEOUT => 3, CURLOPT_TIMEOUT => 45,
        CURLOPT_HEADERFUNCTION => function ($c, string $line) use (&$status): int {
            if (preg_match('#^HTTP/\S+ (\d{3})#', $line, $m)) {
                $status = (int) $m[1];
            }
            return strlen($line);
        },
        CURLOPT_WRITEFUNCTION => function ($c, string $data) use (&$status, $onChunk): int {
            if ($status === 200) {
                $onChunk($data);
            }
            return strlen($data);
        },
    ]);
    curl_exec($ch);
    curl_close($ch);
    return $status;
};

$session->save(); // release the session lock so a long stream does not block the rest of the OpenEMR UI
$started = false;
$emit = function (string $chunk) use (&$started): void {
    if (!$started) {
        header('Content-Type: text/event-stream');
        header('Cache-Control: no-store');
        header('X-Accel-Buffering: no');
        $started = true;
    }
    echo $chunk;
    @ob_flush();
    flush();
};
while (ob_get_level() > 0) {
    ob_end_clean();
}

$proxy = new ChatProxy(
    $signer,
    $resolver,
    $config->agentBaseUrl,
    fn (string $u): string => $issuer->issue($u),
    $post,
    fn (): string => bin2hex(random_bytes(16)),
    fn (): int => time(),
    $config->w2Chat,
);
$result = $proxy->handle($facts, $input, bin2hex(random_bytes(8)), $emit);
if ($result['status'] !== 200 && !$started) {
    copilot_fail($result['status'], $result['error'] ?? 'error');
}
