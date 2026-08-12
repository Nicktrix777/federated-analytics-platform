package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.type.Type;

/** name doubles as the Zoho JSON field name - Zoho's own field names are used verbatim as column names. */
public record ColumnDef(String name, String typeName) {
    public Type resolveType() {
        return TrinoTypes.resolve(typeName);
    }
}
