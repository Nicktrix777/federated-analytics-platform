package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.connector.Connector;
import io.trino.spi.connector.ConnectorContext;
import io.trino.spi.connector.ConnectorFactory;

import java.time.Duration;
import java.util.Map;

public class ZohoBooksConnectorFactory implements ConnectorFactory {
    private static final long DEFAULT_TTL_SECONDS = 3600;

    @Override
    public String getName() {
        return "zoho_books";
    }

    @Override
    public Connector create(String catalogName, Map<String, String> config, ConnectorContext context) {
        String jdbcUrl = requireConfig(config, "metadata.jdbc-url");
        String jdbcUser = requireConfig(config, "metadata.jdbc-user");
        String jdbcPassword = requireConfig(config, "metadata.jdbc-password");
        String encryptionKey = requireConfig(config, "datasource-encryption-key");
        Duration cacheTtl = Duration.ofSeconds(
                optionalLongConfig(config, "schema-discovery.ttl-seconds", DEFAULT_TTL_SECONDS));

        MetadataStore metadataStore = new MetadataStore(jdbcUrl, jdbcUser, jdbcPassword, encryptionKey);
        DiscoveredSchemaStore discoveredSchemaStore = new DiscoveredSchemaStore(jdbcUrl, jdbcUser, jdbcPassword);
        return new ZohoBooksConnector(metadataStore, discoveredSchemaStore, cacheTtl);
    }

    private static String requireConfig(Map<String, String> config, String key) {
        String value = config.get(key);
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("zoho_books catalog is missing required property: " + key);
        }
        return value;
    }

    private static long optionalLongConfig(Map<String, String> config, String key, long defaultValue) {
        String value = config.get(key);
        return (value == null || value.isBlank()) ? defaultValue : Long.parseLong(value);
    }
}
