<?php

/**
 * Co-Pilot click-to-source endpoint. GET document_id, page. Returns one page of a stored document as an image.
 *
 * Authority comes only from the server session: user, open patient and the ACL OpenEMR itself uses for viewing
 * documents. The browser supplies only a document id and a page number. Read-only: nothing is written anywhere.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

require_once __DIR__ . '/../../../../globals.php';
require_once __DIR__ . '/../src/RequestSigner.php';
require_once __DIR__ . '/../src/PatientUuidResolver.php';
require_once __DIR__ . '/../src/AgentClient.php';
require_once __DIR__ . '/../src/UploadProxy.php';
require_once __DIR__ . '/../src/SourceProxy.php';
require_once __DIR__ . '/../src/ModuleConfig.php';
require_once __DIR__ . '/../src/UserTokenIssuer.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\Copilot\AgentClient;
use OpenEMR\Modules\Copilot\ModuleConfig;
use OpenEMR\Modules\Copilot\PatientUuidResolver;
use OpenEMR\Modules\Copilot\RequestSigner;
use OpenEMR\Modules\Copilot\SourceProxy;
use OpenEMR\Modules\Copilot\UserTokenIssuer;

function copilot_source_error(int $status, string $error): never
{
    http_response_code($status);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    echo json_encode(['error' => $error]);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'GET') {
    copilot_source_error(405, 'method_not_allowed');
}
$config = ModuleConfig::load(dirname(__DIR__));
if ($config === null) {
    copilot_source_error(503, 'not_configured');
}

$session = SessionWrapperFactory::getInstance()->getActiveSession();
$userId = (int) ($session->get('authUserID') ?? 0);
$userRow = $userId > 0 ? sqlQuery('SELECT uuid FROM users WHERE id = ?', [$userId]) : false;
$userUuid = (is_array($userRow) && is_string($userRow['uuid'] ?? null) && strlen($userRow['uuid']) === 16)
    ? UuidRegistry::uuidToString($userRow['uuid']) : null;

// Viewing a document needs the same permission OpenEMR asks for when a user opens one in the chart.
$facts = [
    'userUuid' => $userUuid,
    'pid' => $session->get('pid'),
    'aclOk' => AclMain::aclCheckCore('patients', 'docs'),
];
$request = ['documentId' => $_GET['document_id'] ?? null, 'page' => $_GET['page'] ?? null];

$http = function (string $url, array $headers, string $body): array {
    $h = [];
    foreach ($headers as $k => $v) {
        $h[] = $k . ': ' . $v;
    }
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_HTTPHEADER => $h,
        CURLOPT_RETURNTRANSFER => true, CURLOPT_CONNECTTIMEOUT => 3, CURLOPT_TIMEOUT => 30,
    ]);
    $raw = curl_exec($ch);
    $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    return [is_string($raw) ? $status : 0, is_string($raw) ? $raw : ''];
};

$resolver = new PatientUuidResolver(function (int $pid): ?string {
    $row = sqlQuery('SELECT uuid FROM patient_data WHERE pid = ?', [$pid]);
    return is_array($row) && is_string($row['uuid'] ?? null) ? $row['uuid'] : null;
});
$issuer = new UserTokenIssuer($config->oauthClientId, (string) ($GLOBALS['OE_SITE_DIR'] ?? ''));
$agent = new AgentClient(
    new RequestSigner($config->signingSecret),
    $config->agentBaseUrl,
    $http,
    fn (): string => bin2hex(random_bytes(16)),
    fn (): int => time(),
);

$documentInfo = function (int $id): ?array {
    $row = sqlQuery('SELECT foreign_id, deleted FROM documents WHERE id = ?', [$id]);
    return is_array($row) && isset($row['foreign_id'])
        ? ['pid' => (int) $row['foreign_id'], 'deleted' => (int) ($row['deleted'] ?? 0) !== 0]
        : null;
};
$documentBytes = function (int $id): ?string {
    try {
        $doc = new Document($id);
        $data = $doc->get_data();
    } catch (\Throwable) {
        return null;
    }
    return is_string($data) ? $data : null;
};

$session->save();
$proxy = new SourceProxy(
    $resolver,
    $agent,
    fn (string $u): string => $issuer->issue($u),
    $documentInfo,
    $documentBytes,
);
$result = $proxy->handle($facts, $request, bin2hex(random_bytes(8)));
if ($result['status'] !== 200) {
    copilot_source_error($result['status'], $result['error'] ?? 'error');
}
http_response_code(200);
header('Content-Type: ' . ($result['mime'] ?? 'image/png'));
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; sandbox");
echo $result['bytes'] ?? '';
exit;
