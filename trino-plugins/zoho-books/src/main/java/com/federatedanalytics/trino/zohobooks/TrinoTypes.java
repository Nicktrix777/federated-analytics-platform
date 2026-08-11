package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.type.BigintType;
import io.trino.spi.type.DoubleType;
import io.trino.spi.type.Type;
import io.trino.spi.type.VarcharType;

/**
 * Types are stored/passed around as plain name strings (see
 * ZohoBooksColumnHandle) rather than io.trino.spi.type.Type objects -
 * Type isn't plain-Jackson-serializable without a TypeManager wired in,
 * and this connector only ever needs these three fixed types.
 */
final class TrinoTypes {
    private TrinoTypes() {
    }

    static Type resolve(String typeName) {
        return switch (typeName) {
            case "bigint" -> BigintType.BIGINT;
            case "double" -> DoubleType.DOUBLE;
            case "varchar" -> VarcharType.VARCHAR;
            default -> throw new IllegalStateException("Unknown column type: " + typeName);
        };
    }
}
