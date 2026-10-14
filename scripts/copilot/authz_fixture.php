<?php

/**
 * Additive, idempotent fixture for the Co-Pilot authorization proof (synthetic data only).
 *
 * Creates two physicians, a nurse (clin group) and a front-desk user, and assigns the seeded
 * patients (pid 100-111) between the two physicians. Existing users and Checkpoint 1 patients
 * other than providerID are not modified.
 *
 * Run inside the openemr container:
 *   docker exec -e COPILOT_FIXTURE_PASSWORD=... development-easy-light-openemr-1 \
 *     php /var/www/localhost/htdocs/openemr/scripts/copilot/authz_fixture.php
 * Prints JSON with user ids and patient uuids (no passwords).
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    exit(1);
}

$_GET['site'] = 'default';
$ignoreAuth = true;
require_once __DIR__ . '/../../interface/globals.php';

use OpenEMR\Common\Acl\AclExtended;
use OpenEMR\Common\Auth\AuthHash;
use OpenEMR\Common\Uuid\UuidRegistry;

$password = getenv('COPILOT_FIXTURE_PASSWORD');
if (!is_string($password) || strlen($password) < 12) {
    fwrite(STDERR, "COPILOT_FIXTURE_PASSWORD (>=12 chars) is required\n");
    exit(2);
}

// username => [first, last, ACL group title, authorized flag]
$users = [
    'copilot_doc_a' => ['Alice', 'Alder', 'Physicians', 1],
    'copilot_doc_b' => ['Bernard', 'Birch', 'Physicians', 1],
    'copilot_nurse' => ['Nora', 'Nightingale', 'Clinicians', 0],
    'copilot_front' => ['Frank', 'Frontdesk', 'Front Office', 0],
];

$ids = [];
foreach ($users as $username => [$first, $last, $group, $authorized]) {
    $row = sqlQuery('SELECT id FROM users WHERE username = ?', [$username]);
    if (!$row) {
        $uuid = (new UuidRegistry(['table_name' => 'users']))->createUuid();
        $newId = sqlInsert(
            'INSERT INTO users (username, password, authorized, fname, lname, facility, facility_id, see_auth, active, uuid, '
            . 'main_menu_role, patient_menu_role, cal_ui) VALUES (?, ?, ?, ?, ?, ?, 3, 1, 1, ?, ?, ?, 3)',
            [$username, 'NoLongerUsed', $authorized, $first, $last, 'Great Clinic', $uuid, 'standard', 'standard']
        );
        $row = ['id' => $newId];
    }
    $hash = (new AuthHash('auth'))->passwordHash($password);
    sqlStatement(
        'INSERT INTO users_secure (id, username, password, last_update_password) VALUES (?, ?, ?, NOW()) '
        . 'ON DUPLICATE KEY UPDATE password = VALUES(password), login_fail_counter = 0, auto_block_emailed = 0',
        [$row['id'], $username, $hash]
    );
    // Login requires a legacy `groups` row in addition to the phpGACL group.
    if (!sqlQuery('SELECT id FROM `groups` WHERE `user` = ?', [$username])) {
        sqlStatement('INSERT INTO `groups` (name, user) VALUES (?, ?)', ['Default', $username]);
    }
    AclExtended::setUserAro([$group], $username, $first, '', $last);
    $ids[$username] = (int)$row['id'];
}

// Deterministic ownership: even pids belong to physician A, odd pids to physician B.
// pid 120 (the seeded duplicate with no provider) is left untouched.
$assignments = [];
for ($pid = 100; $pid <= 111; $pid++) {
    $owner = ($pid % 2 === 0) ? $ids['copilot_doc_a'] : $ids['copilot_doc_b'];
    sqlStatement('UPDATE patient_data SET providerID = ? WHERE pid = ?', [$owner, $pid]);
    $assignments[$pid] = $owner;
}

// Care team: the patient-level authorization relationship the agent reads back through FHIR CareTeam.
// One active team per seeded patient with exactly one physician member (the owner above).
// The nurse and front-desk users are deliberately on no team.
foreach ($assignments as $pid => $ownerId) {
    $team = sqlQuery('SELECT id FROM care_teams WHERE pid = ? AND team_name = ?', [$pid, 'Copilot PCP team']);
    if (!$team) {
        $teamUuid = (new UuidRegistry(['table_name' => 'care_teams']))->createUuid();
        $teamId = sqlInsert(
            'INSERT INTO care_teams (uuid, pid, status, team_name) VALUES (?, ?, ?, ?)',
            [$teamUuid, $pid, 'active', 'Copilot PCP team']
        );
    } else {
        $teamId = $team['id'];
    }
    sqlStatement('DELETE FROM care_team_member WHERE care_team_id = ?', [$teamId]);
    sqlInsert(
        'INSERT INTO care_team_member (care_team_id, user_id, role, status, provider_since) VALUES (?, ?, ?, ?, CURDATE())',
        [$teamId, $ownerId, 'physician', 'active']
    );
}

$uuids = [];
$res = sqlStatement('SELECT pid, uuid FROM patient_data WHERE pid >= 100');
while ($r = sqlFetchArray($res)) {
    $uuids[(int)$r['pid']] = UuidRegistry::uuidToString($r['uuid']);
}
$userUuids = [];
foreach ($ids as $username => $id) {
    $u = sqlQuery('SELECT uuid FROM users WHERE id = ?', [$id]);
    $userUuids[$username] = UuidRegistry::uuidToString($u['uuid']);
}

echo json_encode([
    'user_ids' => $ids,
    'user_uuids' => $userUuids,
    'patient_owner' => $assignments,
    'patient_uuids' => $uuids,
], JSON_THROW_ON_ERROR) . "\n";
