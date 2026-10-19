FROM postgres:17.11-trixie@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3
# psql, pg_dump and pg_restore come from this same PostgreSQL 17 image.
# Fail rather than silently selecting a different client major.
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pg_dump --version | grep -E ' 17\.' \
    && pg_restore --version | grep -E ' 17\.'
