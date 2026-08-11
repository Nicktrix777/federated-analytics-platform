package com.federatedanalytics.trino.tally;

import io.trino.spi.connector.Connector;
import io.trino.spi.connector.ConnectorMetadata;
import io.trino.spi.connector.ConnectorRecordSetProvider;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorSplitManager;
import io.trino.spi.connector.ConnectorTransactionHandle;
import io.trino.spi.transaction.IsolationLevel;

import java.time.Duration;

public class TallyConnector implements Connector {
    private final ConnectorMetadata metadata;
    private final ConnectorSplitManager splitManager = new TallySplitManager();
    private final ConnectorRecordSetProvider recordSetProvider;

    public TallyConnector(
            MetadataStore metadataStore,
            TallyTransport transport,
            DiscoveredSchemaStore discoveredSchemaStore,
            Duration sampleTimeout,
            Duration cacheTtl) {
        TallySchemaSampler sampler = new TallySchemaSampler(transport, sampleTimeout);
        TallyColumnResolver columnResolver = new TallyColumnResolver(metadataStore, discoveredSchemaStore, sampler, cacheTtl);
        this.metadata = new TallyMetadata(metadataStore, columnResolver);
        this.recordSetProvider = new TallyRecordSetProvider(metadataStore, transport, columnResolver);
    }

    @Override
    public ConnectorTransactionHandle beginTransaction(IsolationLevel isolationLevel, boolean readOnly, boolean autoCommit) {
        return TallyTransactionHandle.INSTANCE;
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
