package com.federatedanalytics.trino.zohobooks;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.trino.spi.connector.ConnectorTableHandle;

public class ZohoBooksTableHandle implements ConnectorTableHandle {
    private final String schemaName;
    private final String tableName;

    @JsonCreator
    public ZohoBooksTableHandle(@JsonProperty("schemaName") String schemaName, @JsonProperty("tableName") String tableName) {
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
