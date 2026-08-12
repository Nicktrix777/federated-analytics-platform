package com.federatedanalytics.trino.tally;

import java.time.Duration;
import java.util.List;
import java.util.Optional;

/**
 * Shared cache-then-sample-then-fallback column resolution, used by BOTH
 * TallyMetadata (schema introspection) and TallyRecordSetProvider (actual
 * query execution) so they always agree on "what columns exist" for a
 * table - a query referencing a newly-discovered column must never fail at
 * execution time just because it succeeded at DESCRIBE time.
 */
final class TallyColumnResolver {
    private final MetadataStore metadataStore;
    private final DiscoveredSchemaStore discoveredSchemaStore;
    private final TallySchemaSampler sampler;
    private final Duration cacheTtl;

    TallyColumnResolver(
            MetadataStore metadataStore,
            DiscoveredSchemaStore discoveredSchemaStore,
            TallySchemaSampler sampler,
            Duration cacheTtl) {
        this.metadataStore = metadataStore;
        this.discoveredSchemaStore = discoveredSchemaStore;
        this.sampler = sampler;
        this.cacheTtl = cacheTtl;
    }

    /** Never lets a caller fail or block on the bridge for long just because
     * live sampling didn't work - a customer's on-prem tally-bridge may simply
     * not be connected right now, which is expected and not an error at this
     * layer. Always falls back to entity's known-correct fixed baseline. */
    List<ColumnDef> resolve(String schemaName, TallyEntity entity) {
        Optional<TallyOrgConnection> org = metadataStore.getOrgBySchema(schemaName);
        if (org.isEmpty()) {
            return entity.columns();
        }
        int dataSourceId = org.get().dataSourceId();
        Optional<List<ColumnDef>> cached = discoveredSchemaStore.getFresh(dataSourceId, entity.tableName(), cacheTtl);
        if (cached.isPresent()) {
            return cached.get();
        }
        try {
            List<ColumnDef> discovered = sampler.discover(dataSourceId, entity);
            discoveredSchemaStore.save(dataSourceId, entity.tableName(), discovered);
            return discovered;
        } catch (Exception e) {
            return entity.columns();
        }
    }
}
