package com.federatedanalytics.trino.tally;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.type.Type;

public class TallyColumnHandle implements ColumnHandle {
    private final String name;
    private final String typeName;

    @JsonCreator
    public TallyColumnHandle(@JsonProperty("name") String name, @JsonProperty("typeName") String typeName) {
        this.name = name;
        this.typeName = typeName;
    }

    @JsonProperty
    public String getName() {
        return name;
    }

    @JsonProperty
    public String getTypeName() {
        return typeName;
    }

    public Type resolveType() {
        return TrinoTypes.resolve(typeName);
    }
}
