package com.federatedanalytics.trino.zohobooks;

import com.fasterxml.jackson.databind.JsonNode;

import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Live-samples real Zoho Books records to discover columns beyond
 * ZohoEntity's fixed baseline - Zoho's list endpoints return summary fields
 * that may carry more than the hardcoded set, or differ from it entirely for
 * a given org's customizations. Only field NAMES and an inferred TYPE are
 * ever returned; the actual sampled values are read into local variables
 * purely to classify (numeric or not) and go out of scope immediately after -
 * nothing here is written anywhere, logged, or returned past this class.
 */
final class ZohoSchemaSampler {
    // How many records to look at before deciding a field's shape - one
    // record can't reliably tell a numeric field from a coincidentally
    // numeric-looking string.
    private static final int SAMPLE_SIZE = 5;

    private final ZohoApiClient apiClient;

    ZohoSchemaSampler(ZohoApiClient apiClient) {
        this.apiClient = apiClient;
    }

    /** entity.columns() plus any newly-discovered fields, from one live sample. */
    List<ColumnDef> discover(ZohoOrgConnection org, ZohoEntity entity) {
        String accessToken = apiClient.mintAccessToken(org);
        List<JsonNode> sample = apiClient.fetchSample(org, entity, accessToken, SAMPLE_SIZE);

        Map<String, ColumnDef> merged = new LinkedHashMap<>();
        for (ColumnDef col : entity.columns()) {
            merged.put(col.name(), col);
        }

        Map<String, List<JsonNode>> observed = new LinkedHashMap<>();
        for (JsonNode item : sample) {
            walk(item, "", observed);
        }
        for (Map.Entry<String, List<JsonNode>> entry : observed.entrySet()) {
            if (!merged.containsKey(entry.getKey())) {
                classify(entry.getKey(), entry.getValue()).ifPresent(col -> merged.put(col.name(), col));
            }
        }
        return new ArrayList<>(merged.values());
    }

    /** Walks one JSON object's fields, flattening exactly one level of nested
     * objects (e.g. billing_address.zip -&gt; billing_address_zip) - Zoho's
     * list responses are flatter than Tally's XML, so one level is enough for
     * this MVP. Arrays (e.g. line_items) are skipped - same deliberate
     * deferral as ZohoEntity's own class comment for invoice/bill line items:
     * a real design pass, not a first cut. */
    private void walk(JsonNode node, String prefix, Map<String, List<JsonNode>> observed) {
        Iterator<Map.Entry<String, JsonNode>> fields = node.fields();
        while (fields.hasNext()) {
            Map.Entry<String, JsonNode> field = fields.next();
            JsonNode value = field.getValue();
            if (value.isObject()) {
                if (prefix.isEmpty()) { // only flatten one level deep for this MVP
                    walk(value, field.getKey(), observed);
                }
                continue;
            }
            if (value.isMissingNode() || value.isNull() || value.isArray()) {
                continue;
            }
            String columnName = prefix.isEmpty() ? field.getKey() : prefix + "_" + field.getKey();
            observed.computeIfAbsent(columnName, k -> new ArrayList<>()).add(value);
        }
    }

    private Optional<ColumnDef> classify(String columnName, List<JsonNode> values) {
        boolean allNumeric = values.stream().allMatch(JsonNode::isNumber);
        return Optional.of(new ColumnDef(columnName, allNumeric ? "double" : "varchar"));
    }
}
