package com.federatedanalytics.trino.tally;

import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ColumnMetadata;
import io.trino.spi.connector.ConnectorMetadata;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.ConnectorTableMetadata;
import io.trino.spi.connector.ConnectorTableVersion;
import io.trino.spi.connector.SchemaTableName;
import io.trino.spi.connector.SchemaTablePrefix;
import io.trino.spi.connector.TableColumnsMetadata;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Schema = one registered customer's Tally company (org list resolved live
 * from postgres-meta via MetadataStore, never cached) - tables are the
 * TallyEntity set, but COLUMNS are live-sampled from the org's real data
 * (falling back to TallyEntity's fixed baseline on any failure) rather than
 * always the fixed set - see TallyColumnResolver. Same shape as
 * ZohoBooksMetadata; see that class for the shared design rationale.
 */
public class TallyMetadata implements ConnectorMetadata {
    private final MetadataStore metadataStore;
    private final TallyColumnResolver columnResolver;

    public TallyMetadata(MetadataStore metadataStore, TallyColumnResolver columnResolver) {
        this.metadataStore = metadataStore;
        this.columnResolver = columnResolver;
    }

    private static List<ColumnMetadata> toColumnMetadata(List<ColumnDef> columns) {
        return columns.stream()
                .map(c -> ColumnMetadata.builder().setName(c.name()).setType(c.resolveType()).build())
                .toList();
    }

    @Override
    public List<String> listSchemaNames(ConnectorSession session) {
        return metadataStore.listOrgs().stream().map(TallyOrgConnection::schemaName).distinct().toList();
    }

    @Override
    public ConnectorTableHandle getTableHandle(
            ConnectorSession session,
            SchemaTableName tableName,
            Optional<ConnectorTableVersion> startVersion,
            Optional<ConnectorTableVersion> endVersion) {
        if (TallyEntity.byTableName(tableName.getTableName()).isEmpty()) {
            return null;
        }
        if (metadataStore.getOrgBySchema(tableName.getSchemaName()).isEmpty()) {
            return null;
        }
        return new TallyTableHandle(tableName.getSchemaName(), tableName.getTableName());
    }

    @Override
    public ConnectorTableMetadata getTableMetadata(ConnectorSession session, ConnectorTableHandle table) {
        TallyTableHandle handle = (TallyTableHandle) table;
        TallyEntity entity = TallyEntity.byTableName(handle.getTableName()).orElseThrow();
        List<ColumnDef> columns = columnResolver.resolve(handle.getSchemaName(), entity);
        return new ConnectorTableMetadata(
                new SchemaTableName(handle.getSchemaName(), handle.getTableName()), toColumnMetadata(columns));
    }

    @Override
    public List<SchemaTableName> listTables(ConnectorSession session, Optional<String> schemaName) {
        List<String> schemas = schemaName.isPresent() ? List.of(schemaName.get()) : listSchemaNames(session);
        List<SchemaTableName> result = new ArrayList<>();
        for (String schema : schemas) {
            if (metadataStore.getOrgBySchema(schema).isEmpty()) {
                continue;
            }
            for (TallyEntity entity : TallyEntity.values()) {
                result.add(new SchemaTableName(schema, entity.tableName()));
            }
        }
        return result;
    }

    @Override
    public Map<String, ColumnHandle> getColumnHandles(ConnectorSession session, ConnectorTableHandle tableHandle) {
        TallyTableHandle handle = (TallyTableHandle) tableHandle;
        TallyEntity entity = TallyEntity.byTableName(handle.getTableName()).orElseThrow();
        Map<String, ColumnHandle> map = new LinkedHashMap<>();
        for (ColumnDef col : columnResolver.resolve(handle.getSchemaName(), entity)) {
            map.put(col.name(), new TallyColumnHandle(col.name(), col.typeName()));
        }
        return map;
    }

    @Override
    public ColumnMetadata getColumnMetadata(ConnectorSession session, ConnectorTableHandle tableHandle, ColumnHandle columnHandle) {
        TallyColumnHandle handle = (TallyColumnHandle) columnHandle;
        return ColumnMetadata.builder().setName(handle.getName()).setType(handle.resolveType()).build();
    }

    @Override
    public Iterator<TableColumnsMetadata> streamTableColumns(ConnectorSession session, SchemaTablePrefix prefix) {
        List<TableColumnsMetadata> result = new ArrayList<>();
        List<String> schemas = prefix.getSchema().map(List::of).orElseGet(() -> listSchemaNames(session));
        for (String schema : schemas) {
            if (metadataStore.getOrgBySchema(schema).isEmpty()) {
                continue;
            }
            for (TallyEntity entity : TallyEntity.values()) {
                SchemaTableName tableName = new SchemaTableName(schema, entity.tableName());
                if (prefix.matches(tableName)) {
                    result.add(TableColumnsMetadata.forTable(tableName, toColumnMetadata(columnResolver.resolve(schema, entity))));
                }
            }
        }
        return result.iterator();
    }
}
