package com.federatedanalytics.trino.tally;

import io.trino.spi.type.DoubleType;
import io.trino.spi.type.Type;
import io.trino.spi.type.VarcharType;

final class TrinoTypes {
    private TrinoTypes() {
    }

    static Type resolve(String typeName) {
        return switch (typeName) {
            case "double" -> DoubleType.DOUBLE;
            case "varchar" -> VarcharType.VARCHAR;
            default -> throw new IllegalStateException("Unknown column type: " + typeName);
        };
    }
}
