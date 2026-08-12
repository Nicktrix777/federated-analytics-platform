package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.connector.Connector;
import io.trino.spi.connector.ConnectorMetadata;
import io.trino.spi.connector.ConnectorRecordSetProvider;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorSplitManager;
import io.trino.spi.connector.ConnectorTransactionHandle;
import io.trino.spi.transaction.IsolationLevel;

import java.time.Duration;

public class ZohoBooksConnector implements Connector {
    private final ConnectorMetadata metadata;
    private final ConnectorSplitManager splitManager = new ZohoBooksSplitManager();
    private final ConnectorRecordSetProvider recordSetProvider;

    public ZohoBooksConnector(
            MetadataStore metadataStore,
            DiscoveredSchemaStore discoveredSchemaStore,
            Duration cacheTtl) {
        ZohoApiClient apiClient = new ZohoApiClient();
        ZohoSchemaSampler sampler = new ZohoSchemaSampler(apiClient);
        ZohoColumnResolver columnResolver = new ZohoColumnResolver(metadataStore, discoveredSchemaStore, sampler, cacheTtl);
        this.metadata = new ZohoBooksMetadata(metadataStore, columnResolver);
        this.recordSetProvider = new ZohoBooksRecordSetProvider(metadataStore, apiClient, columnResolver);
    }

    @Override
    public ConnectorTransactionHandle beginTransaction(IsolationLevel isolationLevel, boolean readOnly, boolean autoCommit) {
        return ZohoBooksTransactionHandle.INSTANCE;
    }

    @Override
    public ConnectorMetadata getMetadata(ConnectorSession session, ConnectorTransactionHandle transactionHandle) {
        return metadata;
    }

    @Override
    public ConnectorSplitManager getSplitManager() {
        return splitManager;
    }

    @Override
    public ConnectorRecordSetProvider getRecordSetProvider() {
        return recordSetProvider;
    }

    @Override
    public void shutdown() {
        // Stateless (no connection pool, no background threads) - nothing to release.
    }
}
