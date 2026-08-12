package com.federatedanalytics.trino.zohobooks;

import java.time.Duration;
import java.util.List;
import java.util.Optional;

/**
 * Shared cache-then-sample-then-fallback column resolution, used by BOTH
 * ZohoBooksMetadata (schema introspection) and ZohoBooksRecordSetProvider
 * (actual query execution) so they always agree on "what columns exist" for
 * a table - a query referencing a newly-discovered column must never fail at
 * execution time just because it succeeded at DESCRIBE time.
 */
final class ZohoColumnResolver {
    private final MetadataStore metadataStore;
    private final DiscoveredSchemaStore discoveredSchemaStore;
    private final ZohoSchemaSampler sampler;
    private final Duration cacheTtl;

    ZohoColumnResolver(
            MetadataStore metadataStore,
            DiscoveredSchemaStore discoveredSchemaStore,
            ZohoSchemaSampler sampler,
            Duration cacheTtl) {
        this.metadataStore = metadataStore;
        this.discoveredSchemaStore = discoveredSchemaStore;
        this.sampler = sampler;
        this.cacheTtl = cacheTtl;
    }

    /** Never lets a caller fail or block for long just because live sampling
     * didn't work - a network blip to Zoho or a revoked refresh token is
     * expected and not an error at this layer. Always falls back to entity's
     * known-correct fixed baseline. */
    List<ColumnDef> resolve(String schemaName, ZohoEntity entity) {
        Optional<ZohoOrgConnection> org = metadataStore.getOrgBySchema(schemaName);
        if (org.isEmpty()) {
            return entity.columns();
        }
        int dataSourceId = org.get().dataSourceId();
        Optional<List<ColumnDef>> cached = discoveredSchemaStore.getFresh(dataSourceId, entity.tableName(), cacheTtl);
        if (cached.isPresent()) {
            return cached.get();
        }
        try {
            List<ColumnDef> discovered = sampler.discover(org.get(), entity);
            discoveredSchemaStore.save(dataSourceId, entity.tableName(), discovered);
            return discovered;
        } catch (Exception e) {
            return entity.columns();
        }
    }
}
