#!/bin/sh
set -eu

if [ "${MIGRATE_ON_STARTUP:-sim}" = "sim" ]; then
  attempt=1
  until python manage.py migrate --noinput; do
    if [ "$attempt" -ge "${DATABASE_STARTUP_ATTEMPTS:-30}" ]; then
      echo "database_migration_failed attempts=$attempt" >&2
      exit 1
    fi
    attempt=$((attempt + 1))
    sleep "${DATABASE_STARTUP_DELAY_SECONDS:-2}"
  done
fi

if [ "${COLLECTSTATIC_ON_STARTUP:-sim}" = "sim" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
