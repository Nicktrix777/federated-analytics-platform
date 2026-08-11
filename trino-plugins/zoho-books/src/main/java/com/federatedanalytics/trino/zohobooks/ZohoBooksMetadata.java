package com.federatedanalytics.trino.zohobooks;

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
 * Schema = one registered customer's Zoho org (org list resolved live from
 * postgres-meta via MetadataStore, never cached) - tables are the ZohoEntity
 * set, but COLUMNS are live-sampled from the org's real data (falling back
 * to ZohoEntity's fixed baseline on any failure) rather than always the
 * fixed set - see ZohoColumnResolver. Registering a new Zoho connection
 * never requires a Trino restart: it's a new row behind the one static
 * catalog, not a new catalog.
 */
public class ZohoBooksMetadata implements ConnectorMetadata {
    private final MetadataStore metadataStore;
    private final ZohoColumnResolver columnResolver;

    public ZohoBooksMetadata(MetadataStore metadataStore, ZohoColumnResolver columnResolver) {
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
        return metadataStore.listOrgs().stream().map(ZohoOrgConnection::schemaName).distinct().toList();
    }

    @Override
    public ConnectorTableHandle getTableHandle(
            ConnectorSession session,
            SchemaTableName tableName,
            Optional<ConnectorTableVersion> startVersion,
            Optional<ConnectorTableVersion> endVersion) {
        if (ZohoEntity.byTableName(tableName.getTableName()).isEmpty()) {
            return null;
        }
        if (metadataStore.getOrgBySchema(tableName.getSchemaName()).isEmpty()) {
            return null;
        }
        return new ZohoBooksTableHandle(tableName.getSchemaName(), tableName.getTableName());
    }

    @Override
    public ConnectorTableMetadata getTableMetadata(ConnectorSession session, ConnectorTableHandle table) {
        ZohoBooksTableHandle handle = (ZohoBooksTableHandle) table;
        ZohoEntity entity = ZohoEntity.byTableName(handle.getTableName()).orElseThrow();
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
            for (ZohoEntity entity : ZohoEntity.values()) {
                result.add(new SchemaTableName(schema, entity.tableName()));
            }
        }
        return result;
    }

    @Override
    public Map<String, ColumnHandle> getColumnHandles(ConnectorSession session, ConnectorTableHandle tableHandle) {
        ZohoBooksTableHandle handle = (ZohoBooksTableHandle) tableHandle;
        ZohoEntity entity = ZohoEntity.byTableName(handle.getTableName()).orElseThrow();
        Map<String, ColumnHandle> map = new LinkedHashMap<>();
        for (ColumnDef col : columnResolver.resolve(handle.getSchemaName(), entity)) {
            map.put(col.name(), new ZohoBooksColumnHandle(col.name(), col.typeName()));
        }
        return map;
    }

    @Override
    public ColumnMetadata getColumnMetadata(ConnectorSession session, ConnectorTableHandle tableHandle, ColumnHandle columnHandle) {
        ZohoBooksColumnHandle handle = (ZohoBooksColumnHandle) columnHandle;
        return ColumnMetadata.builder().setName(handle.getName()).setType(handle.resolveType()).build();
    }

    // Separate from getColumnHandles/getColumnMetadata - Trino's
    // auto-generated information_schema.columns view (what
    // SyncCatalogsFromTrino queries) only populates from this hook.
    @Override
    public Iterator<TableColumnsMetadata> streamTableColumns(ConnectorSession session, SchemaTablePrefix prefix) {
        List<TableColumnsMetadata> result = new ArrayList<>();
        List<String> schemas = prefix.getSchema().map(List::of).orElseGet(() -> listSchemaNames(session));
        for (String schema : schemas) {
            if (metadataStore.getOrgBySchema(schema).isEmpty()) {
                continue;
            }
            for (ZohoEntity entity : ZohoEntity.values()) {
                SchemaTableName tableName = new SchemaTableName(schema, entity.tableName());
                if (prefix.matches(tableName)) {
                    result.add(TableColumnsMetadata.forTable(tableName, toColumnMetadata(columnResolver.resolve(schema, entity))));
                }
            }
        }
        return result.iterator();
    }
}
