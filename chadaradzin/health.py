from __future__ import annotations

import logging

from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse
from django.views.decorators.http import require_GET


LOGGER = logging.getLogger(__name__)


@require_GET
def live(_request):
    return JsonResponse({"status": "ok"})


@require_GET
def ready(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
    except DatabaseError:
        LOGGER.warning(
            "readiness_database_unavailable",
            extra={"event_code": "READINESS_DATABASE_UNAVAILABLE", "status": "failed"},
        )
        return JsonResponse({"status": "unavailable"}, status=503)
    except Exception:
        LOGGER.exception(
            "readiness_check_failed",
            extra={"event_code": "READINESS_CHECK_FAILED", "status": "failed"},
        )
        return JsonResponse({"status": "unavailable"}, status=503)

    if pending:
        LOGGER.warning(
            "readiness_migrations_pending",
            extra={
                "event_code": "READINESS_MIGRATIONS_PENDING",
                "status": "failed",
                "count": len(pending),
            },
        )
        return JsonResponse({"status": "migrations_pending"}, status=503)
    return JsonResponse({"status": "ok"})
