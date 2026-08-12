package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.connector.ColumnMetadata;

import java.util.List;
import java.util.Optional;

/**
 * MVP entity set - core AR/AP objects only (Contacts, Items, Invoices,
 * Bills). Zoho's list endpoints return summary fields; invoice/bill line
 * items live behind a separate per-record detail call and are deliberately
 * deferred (N+1 detail calls per row is a real rate-limit/latency cost that
 * deserves its own design pass, not a first cut). Column sets are hardcoded
 * against Zoho Books API v3's documented, stable field names rather than
 * discovered dynamically - there is no schema-discovery endpoint to ask.
 */
public enum ZohoEntity {
    CONTACTS("contacts", "contacts", List.of(
            new ColumnDef("contact_id", "varchar"),
            new ColumnDef("contact_name", "varchar"),
            new ColumnDef("company_name", "varchar"),
            new ColumnDef("contact_type", "varchar"),
            new ColumnDef("email", "varchar"),
            new ColumnDef("phone", "varchar"),
            new ColumnDef("status", "varchar"),
            new ColumnDef("created_time", "varchar"),
            new ColumnDef("last_modified_time", "varchar"))),

    ITEMS("items", "items", List.of(
            new ColumnDef("item_id", "varchar"),
            new ColumnDef("name", "varchar"),
            new ColumnDef("rate", "double"),
            new ColumnDef("description", "varchar"),
            new ColumnDef("status", "varchar"),
            new ColumnDef("created_time", "varchar"),
            new ColumnDef("last_modified_time", "varchar"))),

    INVOICES("invoices", "invoices", List.of(
            new ColumnDef("invoice_id", "varchar"),
            new ColumnDef("invoice_number", "varchar"),
            new ColumnDef("customer_id", "varchar"),
            new ColumnDef("customer_name", "varchar"),
            new ColumnDef("status", "varchar"),
            new ColumnDef("date", "varchar"),
            new ColumnDef("due_date", "varchar"),
            new ColumnDef("total", "double"),
            new ColumnDef("balance", "double"),
            new ColumnDef("created_time", "varchar"),
            new ColumnDef("last_modified_time", "varchar"))),

    BILLS("bills", "bills", List.of(
            new ColumnDef("bill_id", "varchar"),
            new ColumnDef("vendor_id", "varchar"),
            new ColumnDef("vendor_name", "varchar"),
            new ColumnDef("bill_number", "varchar"),
            new ColumnDef("status", "varchar"),
            new ColumnDef("date", "varchar"),
            new ColumnDef("due_date", "varchar"),
            new ColumnDef("total", "double"),
            new ColumnDef("balance", "double"),
            new ColumnDef("created_time", "varchar"),
            new ColumnDef("last_modified_time", "varchar")));

    private final String tableName;
    private final String apiPath;
    private final List<ColumnDef> columns;

    ZohoEntity(String tableName, String apiPath, List<ColumnDef> columns) {
        this.tableName = tableName;
        this.apiPath = apiPath;
        this.columns = columns;
    }

    public String tableName() {
        return tableName;
    }

    public String apiPath() {
        return apiPath;
    }

    /** Zoho wraps each list response as {"<key>": [...], "page_context": {...}} - here it always matches apiPath. */
    public String responseArrayKey() {
        return apiPath;
    }

    public List<ColumnDef> columns() {
        return columns;
    }

    public List<ColumnMetadata> columnMetadata() {
        return columns.stream()
                .map(c -> ColumnMetadata.builder().setName(c.name()).setType(c.resolveType()).build())
                .toList();
    }

    public static Optional<ZohoEntity> byTableName(String name) {
        for (ZohoEntity entity : values()) {
            if (entity.tableName.equals(name)) {
                return Optional.of(entity);
            }
        }
        return Optional.empty();
    }
}
