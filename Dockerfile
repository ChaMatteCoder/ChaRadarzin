# syntax=docker/dockerfile:1.7
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 chadaradzin \
    && useradd --uid 10001 --gid chadaradzin --create-home chadaradzin

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --requirement requirements.txt

COPY --chown=chadaradzin:chadaradzin . .
RUN mkdir -p /app/backups /app/staticfiles \
    && chown -R chadaradzin:chadaradzin /app/backups /app/staticfiles

USER chadaradzin
EXPOSE 8000

ENTRYPOINT ["sh", "/app/scripts/docker-entrypoint.sh"]
CMD ["gunicorn", "chadaradzin.wsgi:application", "--config", "/app/gunicorn.conf.py"]
