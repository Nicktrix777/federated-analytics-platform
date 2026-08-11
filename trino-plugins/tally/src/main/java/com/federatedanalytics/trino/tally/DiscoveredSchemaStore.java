package com.federatedanalytics.trino.tally;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Reads/writes discovered_columns - the cache of live-sampled schema shape
 * (column NAMES and inferred TYPES only, never a real customer value) that
 * backs dynamic schema discovery (see TallySchemaSampler). Same plain
 * per-call-JDBC style as MetadataStore, deliberately not pooled - discovery
 * reads/writes are rare (gated by a TTL), unlike MetadataStore's per-query
 * lookups.
 */
public class DiscoveredSchemaStore {
    private final String jdbcUrl;
    private final String jdbcUser;
    private final String jdbcPassword;

    public DiscoveredSchemaStore(String jdbcUrl, String jdbcUser, String jdbcPassword) {
        this.jdbcUrl = jdbcUrl;
        this.jdbcUser = jdbcUser;
        this.jdbcPassword = jdbcPassword;
    }

    /** The cached columns for this table if a discovery ran within `ttl` of now,
     * else empty (missing entirely, or every row is stale). */
    public Optional<List<ColumnDef>> getFresh(int dataSourceId, String tableName, Duration ttl) {
        String sql = "SELECT column_name, type_name, is_attribute, discovered_at "
                + "FROM discovered_columns WHERE data_source_id = ? AND table_name = ?";
        List<ColumnDef> columns = new ArrayList<>();
        Instant oldest = null;
        try (Connection conn = connect();
                PreparedStatement stmt = conn.prepareStatement(sql)) {
            stmt.setInt(1, dataSourceId);
            stmt.setString(2, tableName);
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    columns.add(new ColumnDef(
                            rs.getString("column_name"), rs.getString("type_name"), rs.getBoolean("is_attribute")));
                    Instant discoveredAt = rs.getTimestamp("discovered_at").toInstant();
                    if (oldest == null || discoveredAt.isBefore(oldest)) {
                        oldest = discoveredAt;
                    }
                }
            }
        } catch (SQLException e) {
            throw new RuntimeException("failed to read discovered_columns for " + tableName, e);
        }
        if (columns.isEmpty() || oldest.isBefore(Instant.now().minus(ttl))) {
            return Optional.empty();
        }
        return Optional.of(columns);
    }

    /** Replaces the cached columns for this table with a freshly-discovered set.
     * `columns` is expected to already be the full merged set (fixed baseline +
     * newly-discovered fields) - see TallySchemaSampler.discover(). */
    public void save(int dataSourceId, String tableName, List<ColumnDef> columns) {
        String deleteSql = "DELETE FROM discovered_columns WHERE data_source_id = ? AND table_name = ?";
        String insertSql = "INSERT INTO discovered_columns "
                + "(data_source_id, table_name, column_name, type_name, is_attribute, discovered_at) "
                + "VALUES (?, ?, ?, ?, ?, NOW())";
        try (Connection conn = connect()) {
            try (PreparedStatement del = conn.prepareStatement(deleteSql)) {
                del.setInt(1, dataSourceId);
                del.setString(2, tableName);
                del.executeUpdate();
            }
            try (PreparedStatement ins = conn.prepareStatement(insertSql)) {
                for (ColumnDef col : columns) {
                    ins.setInt(1, dataSourceId);
                    ins.setString(2, tableName);
                    ins.setString(3, col.name());
                    ins.setString(4, col.typeName());
                    ins.setBoolean(5, col.isAttribute());
                    ins.addBatch();
                }
                ins.executeBatch();
            }
        } catch (SQLException e) {
            throw new RuntimeException("failed to save discovered_columns for " + tableName, e);
        }
    }

    private Connection connect() throws SQLException {
        return DriverManager.getConnection(jdbcUrl, jdbcUser, jdbcPassword);
    }
}
