package com.federatedanalytics.trino.tally;

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

public class TallyRecordSetProvider implements ConnectorRecordSetProvider {
    private final MetadataStore metadataStore;
    private final TallyTransport transport;
    private final TallyColumnResolver columnResolver;

    public TallyRecordSetProvider(MetadataStore metadataStore, TallyTransport transport, TallyColumnResolver columnResolver) {
        this.metadataStore = metadataStore;
        this.transport = transport;
        this.columnResolver = columnResolver;
    }

    @Override
    public RecordSet getRecordSet(
            ConnectorTransactionHandle transaction,
            ConnectorSession session,
            ConnectorSplit split,
            ConnectorTableHandle table,
            List<? extends ColumnHandle> columns) {
        TallySplit tallySplit = (TallySplit) split;
        TallyOrgConnection org = metadataStore.getOrgBySchema(tallySplit.getSchemaName())
                .orElseThrow(() -> new RuntimeException(
                        "no registered Tally connection for schema " + tallySplit.getSchemaName()));
        TallyEntity entity = TallyEntity.byTableName(tallySplit.getTableName())
                .orElseThrow(() -> new RuntimeException("unknown Tally table " + tallySplit.getTableName()));

        // Same resolved column set (fixed baseline + any live-discovered fields)
        // Metadata reported at DESCRIBE time - a query referencing a
        // newly-discovered column must never fail here just because it
        // succeeded at planning time.
        List<ColumnDef> resolvedColumns = columnResolver.resolve(tallySplit.getSchemaName(), entity);
        String responseXml = transport.send(org.dataSourceId(), entity.buildRequestXml());
        List<Map<String, Object>> rows = TallyXmlParser.rows(responseXml, entity, resolvedColumns);

        List<Type> types = new ArrayList<>();
        List<String> columnNames = new ArrayList<>();
        for (ColumnHandle columnHandle : columns) {
            TallyColumnHandle handle = (TallyColumnHandle) columnHandle;
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
