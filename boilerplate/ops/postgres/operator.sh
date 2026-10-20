#!/usr/bin/env bash
# Infrastructure only. Product SQL stays in the product's repository/hook.
set -euo pipefail
umask 077
action=${1:?action required}
psql_cmd=(psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet)
for name in PGDATABASE_NAME PG_OWNER_ROLE PG_APP_ROLE PG_INSTALL_ROLE PG_BACKUP_ROLE PG_RESTORE_ROLE; do
  [[ ${!name} =~ ^[a-z][a-z0-9_]{0,62}$ ]] || exit 2
done
case "$action" in
  identity)
    "${psql_cmd[@]}" --tuples-only --no-align <<'SQL'
SELECT json_build_object('system_identifier',system_identifier::text,'timeline',(SELECT timeline_id FROM pg_control_checkpoint()),'tls',(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())) FROM pg_control_system();
SQL
    ;;
  role)
    role=${2:?role required}
    [[ $role =~ ^[a-z][a-z0-9_]{0,62}$ ]] || exit 2
    # Each invocation receives just this issuance material and its provisioner connection.
    [[ -s /run/pg-client/new-role.password ]] || exit 2
    NEW_ROLE_PASSWORD=$(cat /run/pg-client/new-role.password)
    export NEW_ROLE_PASSWORD
    "${psql_cmd[@]}" -v role="$role" <<'SQL'
\getenv secret NEW_ROLE_PASSWORD
CREATE ROLE :"role" LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'secret';
SQL
    ;;
  database)
    "${psql_cmd[@]}" -v db="$PGDATABASE_NAME" -v owner="$PG_OWNER_ROLE" -v app="$PG_APP_ROLE" -v installer="$PG_INSTALL_ROLE" -v backup="$PG_BACKUP_ROLE" -v restore="$PG_RESTORE_ROLE" <<'SQL'
CREATE ROLE :"owner" NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE DATABASE :"db" OWNER :"owner";
-- This recipe owns the complete NEW local instance, never a shared external cluster.
REVOKE CONNECT, TEMPORARY ON DATABASE postgres, template1 FROM PUBLIC;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO :"app", :"installer", :"backup", :"restore";
GRANT :"owner" TO :"installer" WITH INHERIT FALSE, SET TRUE;
GRANT :"owner" TO :"restore" WITH INHERIT FALSE, SET TRUE;
SQL
    ;;
  controls)
    "${psql_cmd[@]}" -v installer="$PG_INSTALL_ROLE" -v backup="$PG_BACKUP_ROLE" -v restore="$PG_RESTORE_ROLE" <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pg_control_system(), pg_control_checkpoint() TO :"installer", :"backup", :"restore";
SQL
    ;;
  retire-install|retire-backup|retire-restore)
    case "$action" in retire-install) role=$PG_INSTALL_ROLE;; retire-backup) role=$PG_BACKUP_ROLE;; retire-restore) role=$PG_RESTORE_ROLE;; esac
    "${psql_cmd[@]}" -v owner="$PG_OWNER_ROLE" -v role="$role" <<'SQL'
REVOKE :"owner" FROM :"role";
REVOKE EXECUTE ON FUNCTION pg_control_system(), pg_control_checkpoint() FROM :"role";
ALTER ROLE :"role" NOLOGIN;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=:'role' AND pid<>pg_backend_pid();
SQL
    ;;
  *) echo 'Unknown infrastructure action' >&2; exit 2;;
esac
