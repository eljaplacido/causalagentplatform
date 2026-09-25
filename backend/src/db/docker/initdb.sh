#!/bin/sh
# PostgreSQL first-start script for the local docker-compose stack.
#
# The postgres image runs this once, when its data volume is created, as the
# superuser POSTGRES_USER. It applies backend/src/db/migrations/*.sql in order
# (mounted at /migrations) and then gives causal_app -- the role the backend
# connects as, which the row-level-security policies bind (010_rls_binds.sql)
# -- the login password in CAUSAL_APP_PASSWORD.
#
# The password is a local-dev throwaway set in docker-compose.yml, like
# POSTGRES_PASSWORD beside it. A deployment sets causal_app's password (or IAM
# login) itself and never runs this file.
set -eu

for migration in /migrations/[0-9][0-9][0-9]_*.sql; do
    echo "initdb: applying $(basename "$migration")"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$migration"
done

if [ -n "${CAUSAL_APP_PASSWORD:-}" ]; then
    # psql's :'var' quotes the value as a literal.
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -v pw="$CAUSAL_APP_PASSWORD" <<'SQL'
ALTER ROLE causal_app LOGIN PASSWORD :'pw';
SQL
    echo "initdb: causal_app can log in"
fi
