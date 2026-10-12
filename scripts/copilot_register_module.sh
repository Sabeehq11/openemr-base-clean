#!/usr/bin/env bash
# Idempotently registers and activates oe-module-copilot in the local dev OpenEMR (no UI clicks needed).
set -euo pipefail
DB_CONTAINER="${DB_CONTAINER:-development-easy-light-mysql-1}"
docker exec "$DB_CONTAINER" mariadb -uroot -proot openemr -e "
INSERT INTO modules (mod_name, mod_directory, mod_parent, mod_type, mod_active, mod_ui_name, mod_relative_link,
  mod_ui_order, mod_ui_active, mod_description, mod_nick_name, mod_enc_menu, directory, date, sql_run, type, sql_version, acl_version)
SELECT 'Clinical Co-Pilot', 'oe-module-copilot', '', '', 1, 'Clinical Co-Pilot', '', 0, 0,
  'Read-only chart assistant dock', 'copilot', 'no', 'oe-module-copilot', NOW(), 1, 0, '1.0.0', '1.0.0'
WHERE NOT EXISTS (SELECT 1 FROM modules WHERE mod_directory = 'oe-module-copilot');
UPDATE modules SET mod_active = 1 WHERE mod_directory = 'oe-module-copilot';
SELECT mod_id, mod_directory, mod_active, type FROM modules WHERE mod_directory = 'oe-module-copilot';"
