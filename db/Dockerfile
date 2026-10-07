# Production database: the same TimescaleDB image docker-compose uses, with the
# db/init scripts baked in. They run once, when the volume is empty; on an
# existing database apply new scripts by hand (see DEVELOPMENT.md).
FROM timescale/timescaledb:latest-pg16

COPY init/ /docker-entrypoint-initdb.d/

# A platform volume (Railway's included) is mounted at the data directory with a
# lost+found folder in it, and initdb refuses a non-empty directory: keep the
# cluster in a subfolder of the volume.
ENV PGDATA=/var/lib/postgresql/data/pgdata
