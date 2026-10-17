<?php

/**
 * Co-Pilot document upload endpoint. POST multipart: file, doc_type (lab_pdf|intake_form). Header X-CSRF-Token.
 *
 * Authority comes only from the server session: user, open patient, CSRF and the ACL checks OpenEMR itself uses for
 * adding documents and lab results. The browser never supplies a patient id or any value written to the chart.
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
require_once __DIR__ . '/../src/LabRecordStore.php';
require_once __DIR__ . '/../src/LabRecordWriter.php';
require_once __DIR__ . '/../src/OpenEmrLabStore.php';
require_once __DIR__ . '/../src/UploadProxy.php';
require_once __DIR__ . '/../src/ModuleConfig.php';
require_once __DIR__ . '/../src/UserTokenIssuer.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\Copilot\AgentClient;
use OpenEMR\Modules\Copilot\LabRecordWriter;
use OpenEMR\Modules\Copilot\ModuleConfig;
use OpenEMR\Modules\Copilot\OpenEmrLabStore;
use OpenEMR\Modules\Copilot\PatientUuidResolver;
use OpenEMR\Modules\Copilot\RequestSigner;
use OpenEMR\Modules\Copilot\UploadProxy;
use OpenEMR\Modules\Copilot\UserTokenIssuer;

function copilot_upload_reply(int $status, array $body): never
{
    http_response_code($status);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    echo json_encode($body);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    copilot_upload_reply(405, ['error' => 'method_not_allowed']);
}
$config = ModuleConfig::load(dirname(__DIR__));
if ($config === null) {
    copilot_upload_reply(503, ['error' => 'not_configured']);
}

$session = SessionWrapperFactory::getInstance()->getActiveSession();
$file = $_FILES['file'] ?? null;
$bytes = '';
if (is_array($file) && ($file['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_OK && is_uploaded_file((string) $file['tmp_name'])) {
    $bytes = (string) file_get_contents((string) $file['tmp_name']);
} elseif (is_array($file) && in_array($file['error'] ?? 0, [UPLOAD_ERR_INI_SIZE, UPLOAD_ERR_FORM_SIZE], true)) {
    copilot_upload_reply(413, ['error' => 'too_large']);
}

$userId = (int) ($session->get('authUserID') ?? 0);
$userRow = $userId > 0 ? sqlQuery('SELECT uuid FROM users WHERE id = ?', [$userId]) : false;
$userUuid = (is_array($userRow) && is_string($userRow['uuid'] ?? null) && strlen($userRow['uuid']) === 16)
    ? UuidRegistry::uuidToString($userRow['uuid']) : null;

// The same ACL checks OpenEMR applies to adding a document and to adding lab results.
$aclOk = AclMain::aclCheckCore('patients', 'docs', '', ['write', 'addonly']) && AclMain::aclCheckCore('patients', 'lab');
$facts = [
    'userUuid' => $userUuid,
    'pid' => $session->get('pid'),
    'csrfOk' => CsrfUtils::verifyCsrfToken($_SERVER['HTTP_X_CSRF_TOKEN'] ?? '', session: $session),
    'aclOk' => $aclOk,
];
$upload = [
    'docType' => $_POST['doc_type'] ?? null,
    'name' => is_array($file) ? ($file['name'] ?? null) : null,
    'bytes' => $bytes,
];

$http = function (string $url, array $headers, string $body): array {
    $h = [];
    foreach ($headers as $k => $v) {
        $h[] = $k . ': ' . $v;
    }
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST => true, CURLOPT_POSTFIELDS => $body, CURLOPT_HTTPHEADER => $h,
        CURLOPT_RETURNTRANSFER => true, CURLOPT_CONNECTTIMEOUT => 3, CURLOPT_TIMEOUT => 90,
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
$writer = new LabRecordWriter(new OpenEmrLabStore(), fn (): int => time());

$findDocument = function (int $pid, string $hash): ?int {
    $row = sqlQuery(
        'SELECT id FROM documents WHERE foreign_id = ? AND hash = ? AND deleted = 0 ORDER BY id LIMIT 1',
        [$pid, $hash]
    );
    return is_array($row) && isset($row['id']) ? (int) $row['id'] : null;
};
$createDocument = function (int $pid, int $category, string $name, string $mime, string $data): int {
    $doc = new Document();
    $err = $doc->createDocument($pid, $category, $name, $mime, $data);
    if ($err !== '' || (int) $doc->get_id() <= 0) {
        throw new RuntimeException('document_not_stored');
    }
    return (int) $doc->get_id();
};

// The same soft delete OpenEMR's own document delete performs (interface/patient_file/deleter.php), used only to
// take back a document this very request created when the upload is refused afterwards.
$deleteDocument = function (int $documentId): void {
    sqlStatement('UPDATE documents SET deleted = 1 WHERE id = ?', [$documentId]);
    sqlStatement('DELETE FROM categories_to_documents WHERE document_id = ?', [$documentId]);
};

$proxy = new UploadProxy(
    $resolver,
    $writer,
    $agent,
    fn (string $u): string => $issuer->issue($u),
    $findDocument,
    $createDocument,
    $deleteDocument,
);
$session->save(); // release the session lock: extraction can take a while
$result = $proxy->handle($facts, $upload, bin2hex(random_bytes(8)));
if ($result['status'] !== 200) {
    copilot_upload_reply($result['status'], ['error' => $result['error'] ?? 'error']);
}
copilot_upload_reply(200, $result['body'] ?? []);
