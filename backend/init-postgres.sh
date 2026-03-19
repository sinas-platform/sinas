#!/bin/bash
# Create the sinas_data database for user data (Database Connections, DDL, CDC).
# This runs once on first Postgres initialization.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE sinas_data'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'sinas_data')\gexec
EOSQL
