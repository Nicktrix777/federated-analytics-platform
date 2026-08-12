package com.federatedanalytics.trino.tally;

import io.trino.spi.connector.Connector;
import io.trino.spi.connector.ConnectorContext;
import io.trino.spi.connector.ConnectorFactory;

import java.time.Duration;
import java.util.Map;

public class TallyConnectorFactory implements ConnectorFactory {
    private static final long DEFAULT_TTL_SECONDS = 3600;
    private static final long DEFAULT_SAMPLE_TIMEOUT_SECONDS = 10;

    @Override
    public String getName() {
        return "tally";
    }

    @Override
    public Connector create(String catalogName, Map<String, String> config, ConnectorContext context) {
        String jdbcUrl = requireConfig(config, "metadata.jdbc-url");
        String jdbcUser = requireConfig(config, "metadata.jdbc-user");
        String jdbcPassword = requireConfig(config, "metadata.jdbc-password");
        String encryptionKey = requireConfig(config, "datasource-encryption-key");
        String coreApiBaseUrl = requireConfig(config, "core-api.base-url");
        String internalServiceToken = requireConfig(config, "internal-service-token");
        Duration cacheTtl = Duration.ofSeconds(
                optionalLongConfig(config, "schema-discovery.ttl-seconds", DEFAULT_TTL_SECONDS));
        Duration sampleTimeout = Duration.ofSeconds(
                optionalLongConfig(config, "schema-discovery.sample-timeout-seconds", DEFAULT_SAMPLE_TIMEOUT_SECONDS));

        MetadataStore metadataStore = new MetadataStore(jdbcUrl, jdbcUser, jdbcPassword, encryptionKey);
        TallyTransport transport = new TallyTransport(coreApiBaseUrl, internalServiceToken);
        DiscoveredSchemaStore discoveredSchemaStore = new DiscoveredSchemaStore(jdbcUrl, jdbcUser, jdbcPassword);
        return new TallyConnector(metadataStore, transport, discoveredSchemaStore, sampleTimeout, cacheTtl);
    }

    private static String requireConfig(Map<String, String> config, String key) {
        String value = config.get(key);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("tally catalog is missing required property: " + key);
        }
        return value;
    }

    private static long optionalLongConfig(Map<String, String> config, String key, long defaultValue) {
        String value = config.get(key);
        return (value == null || value.isBlank()) ? defaultValue : Long.parseLong(value);
    }
}
