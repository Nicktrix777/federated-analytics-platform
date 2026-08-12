package com.federatedanalytics.trino.zohobooks;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.trino.spi.connector.ConnectorSplit;

/** Single split per table - the live Zoho API call paginates internally (see ZohoApiClient.fetchAll). */
public class ZohoBooksSplit implements ConnectorSplit {
    private final String schemaName;
    private final String tableName;

    @JsonCreator
    public ZohoBooksSplit(@JsonProperty("schemaName") String schemaName, @JsonProperty("tableName") String tableName) {
        this.schemaName = schemaName;
        this.tableName = tableName;
    }

    @JsonProperty
    public String getSchemaName() {
        return schemaName;
    }

    @JsonProperty
    public String getTableName() {
        return tableName;
    }
}
