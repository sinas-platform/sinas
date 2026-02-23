#!/bin/sh
# ClickHouse entrypoint wrapper: generates S3 tiered storage config if S3 env vars are set.
# If CLICKHOUSE_S3_ENDPOINT is not set, ClickHouse starts with default local-only storage.

set -e

if [ -n "$CLICKHOUSE_S3_ENDPOINT" ] && [ -n "$CLICKHOUSE_S3_BUCKET" ]; then
    REGION="${CLICKHOUSE_S3_REGION:-us-east-1}"

    echo "Generating S3 tiered storage config..."
    mkdir -p /etc/clickhouse-server/config.d

    cat > /etc/clickhouse-server/config.d/s3-storage.xml <<EOF
<clickhouse>
    <storage_configuration>
        <disks>
            <s3_cold>
                <type>s3</type>
                <endpoint>${CLICKHOUSE_S3_ENDPOINT}/${CLICKHOUSE_S3_BUCKET}/clickhouse/data/</endpoint>
                <access_key_id>${CLICKHOUSE_S3_ACCESS_KEY_ID}</access_key_id>
                <secret_access_key>${CLICKHOUSE_S3_SECRET_ACCESS_KEY}</secret_access_key>
                <region>${REGION}</region>
                <metadata_path>/var/lib/clickhouse/disks/s3_cold/</metadata_path>
            </s3_cold>
        </disks>
        <policies>
            <tiered>
                <volumes>
                    <hot>
                        <disk>default</disk>
                    </hot>
                    <cold>
                        <disk>s3_cold</disk>
                    </cold>
                </volumes>
            </tiered>
        </policies>
    </storage_configuration>
</clickhouse>
EOF

    echo "S3 tiered storage config written to /etc/clickhouse-server/config.d/s3-storage.xml"
else
    echo "No S3 env vars set, using default local storage only."
fi

exec /entrypoint.sh "$@"
