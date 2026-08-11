package com.federatedanalytics.trino.tally;

import io.trino.spi.connector.ColumnMetadata;

import java.util.List;
import java.util.Optional;

/**
 * MVP entity set - Ledgers, Vouchers, Stock Items, Groups. Report names and
 * exact XML tag/attribute names below follow Tally's long-documented XML
 * export dialect (TALLYREQUEST=Export Data, REPORTNAME + STATICVARIABLES),
 * but have NOT been verified against a live Tally instance - there is no
 * such instance in this environment. Response parsing (see
 * TallyXmlParser.rows) is deliberately tolerant of missing fields/tags
 * rather than throwing, so it degrades gracefully rather than breaking
 * outright if a real Tally's exact output shape differs in some detail;
 * treat this connector as needing a live-Tally verification pass before
 * being trusted for a real customer.
 */
public enum TallyEntity {
    LEDGERS("ledgers", "List of Ledgers", "LEDGER", List.of(
            new ColumnDef("name", "varchar", true),
            new ColumnDef("parent", "varchar"),
            new ColumnDef("opening_balance", "double"),
            new ColumnDef("closing_balance", "double"))),

    VOUCHERS("vouchers", "Day Book", "VOUCHER", List.of(
            new ColumnDef("date", "varchar"),
            new ColumnDef("voucher_type_name", "varchar"),
            new ColumnDef("voucher_number", "varchar"),
            new ColumnDef("party_ledger_name", "varchar"),
            new ColumnDef("amount", "double"))),

    STOCK_ITEMS("stock_items", "List of Stock Items", "STOCKITEM", List.of(
            new ColumnDef("name", "varchar", true),
            new ColumnDef("parent", "varchar"),
            new ColumnDef("base_units", "varchar"),
            new ColumnDef("closing_balance", "double"),
            new ColumnDef("closing_value", "double"))),

    GROUPS("groups", "List of Groups", "GROUP", List.of(
            new ColumnDef("name", "varchar", true),
            new ColumnDef("parent", "varchar")));

    private final String tableName;
    private final String reportName;
    private final String xmlTag;
    private final List<ColumnDef> columns;

    TallyEntity(String tableName, String reportName, String xmlTag, List<ColumnDef> columns) {
        this.tableName = tableName;
        this.reportName = reportName;
        this.xmlTag = xmlTag;
        this.columns = columns;
    }

    public String tableName() {
        return tableName;
    }

    public String xmlTag() {
        return xmlTag;
    }

    public List<ColumnDef> columns() {
        return columns;
    }

    public List<ColumnMetadata> columnMetadata() {
        return columns.stream()
                .map(c -> ColumnMetadata.builder().setName(c.name()).setType(c.resolveType()).build())
                .toList();
    }

    /** Requests the full XML export for this entity's report - Tally has no filter/pagination story used here. */
    public String buildRequestXml() {
        return "<ENVELOPE>"
                + "<HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>"
                + "<BODY><EXPORTDATA><REQUESTDESC>"
                + "<REPORTNAME>" + reportName + "</REPORTNAME>"
                + "<STATICVARIABLES><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>"
                + "</REQUESTDESC></EXPORTDATA></BODY>"
                + "</ENVELOPE>";
    }

    public static Optional<TallyEntity> byTableName(String name) {
        for (TallyEntity entity : values()) {
            if (entity.tableName.equals(name)) {
                return Optional.of(entity);
            }
        }
        return Optional.empty();
    }
}
