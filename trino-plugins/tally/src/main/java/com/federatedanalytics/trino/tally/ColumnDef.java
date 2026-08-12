package com.federatedanalytics.trino.tally;

import io.trino.spi.type.Type;

/** name doubles as the Tally XML tag/attribute name looked up for this field (see TallyEntity). */
public record ColumnDef(String name, String typeName, boolean isAttribute) {
    public ColumnDef(String name, String typeName) {
        this(name, typeName, false);
    }

    public Type resolveType() {
        return TrinoTypes.resolve(typeName);
    }
}
