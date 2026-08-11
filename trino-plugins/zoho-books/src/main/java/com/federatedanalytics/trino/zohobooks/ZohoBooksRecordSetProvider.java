package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ConnectorRecordSetProvider;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorSplit;
import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.ConnectorTransactionHandle;
import io.trino.spi.connector.InMemoryRecordSet;
import io.trino.spi.connector.RecordSet;
import io.trino.spi.type.Type;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class ZohoBooksRecordSetProvider implements ConnectorRecordSetProvider {
    private final MetadataStore metadataStore;
    private final ZohoApiClient apiClient;
    private final ZohoColumnResolver columnResolver;

    public ZohoBooksRecordSetProvider(MetadataStore metadataStore, ZohoApiClient apiClient, ZohoColumnResolver columnResolver) {
        this.metadataStore = metadataStore;
        this.apiClient = apiClient;
        this.columnResolver = columnResolver;
    }

    @Override
    public RecordSet getRecordSet(
            ConnectorTransactionHandle transaction,
            ConnectorSession session,
            ConnectorSplit split,
            ConnectorTableHandle table,
            List<? extends ColumnHandle> columns) {
        ZohoBooksSplit zohoSplit = (ZohoBooksSplit) split;
        ZohoOrgConnection org = metadataStore.getOrgBySchema(zohoSplit.getSchemaName())
                .orElseThrow(() -> new RuntimeException(
                        "no registered Zoho Books connection for schema " + zohoSplit.getSchemaName()));
        ZohoEntity entity = ZohoEntity.byTableName(zohoSplit.getTableName())
                .orElseThrow(() -> new RuntimeException("unknown Zoho Books table " + zohoSplit.getTableName()));

        // Same resolved column set (fixed baseline + any live-discovered
        // fields) Metadata reported at DESCRIBE time.
        List<ColumnDef> resolvedColumns = columnResolver.resolve(zohoSplit.getSchemaName(), entity);
        String accessToken = apiClient.mintAccessToken(org);
        List<Map<String, Object>> rows = apiClient.fetchAll(org, entity, accessToken, resolvedColumns);

        // Live rows are keyed by column name (not a fixed positional order),
        // so respecting Trino's requested column list/order is just a lookup
        // here rather than an index remap.
        List<Type> types = new ArrayList<>();
        List<String> columnNames = new ArrayList<>();
        for (ColumnHandle columnHandle : columns) {
            ZohoBooksColumnHandle handle = (ZohoBooksColumnHandle) columnHandle;
            types.add(handle.resolveType());
            columnNames.add(handle.getName());
        }

        List<List<Object>> projectedRows = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            List<Object> projected = new ArrayList<>();
            for (String columnName : columnNames) {
                projected.add(row.get(columnName));
            }
            projectedRows.add(projected);
        }
        return new InMemoryRecordSet(types, projectedRows);
    }
}
