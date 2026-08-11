package com.federatedanalytics.trino.tally;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.trino.spi.connector.ConnectorSplit;

/** Single split per table - Tally's export returns the full report in one response, no pagination. */
public class TallySplit implements ConnectorSplit {
    private final String schemaName;
    private final String tableName;

    @JsonCreator
    public TallySplit(@JsonProperty("schemaName") String schemaName, @JsonProperty("tableName") String tableName) {
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
