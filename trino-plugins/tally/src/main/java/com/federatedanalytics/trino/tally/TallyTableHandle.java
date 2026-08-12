package com.federatedanalytics.trino.tally;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.trino.spi.connector.ConnectorTableHandle;

public class TallyTableHandle implements ConnectorTableHandle {
    private final String schemaName;
    private final String tableName;

    @JsonCreator
    public TallyTableHandle(@JsonProperty("schemaName") String schemaName, @JsonProperty("tableName") String tableName) {
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
