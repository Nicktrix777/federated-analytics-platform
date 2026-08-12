package com.federatedanalytics.trino.tally;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Reads registered tally data_sources rows directly from postgres-meta and
 * decrypts their bridge token - each row is one customer's Tally company,
 * distinguished by trino_schema, sharing the one static tally catalog.
 * A plain per-call connection (no pooling) is deliberate, same reasoning
 * as trino-plugins/zoho-books/MetadataStore.java.
 */
public class MetadataStore {
    private final String jdbcUrl;
    private final String jdbcUser;
    private final String jdbcPassword;
    private final AesGcmCrypto crypto;

    public MetadataStore(String jdbcUrl, String jdbcUser, String jdbcPassword, String encryptionKey) {
        this.jdbcUrl = jdbcUrl;
        this.jdbcUser = jdbcUser;
        this.jdbcPassword = jdbcPassword;
        this.crypto = new AesGcmCrypto(encryptionKey);
    }

    public List<TallyOrgConnection> listOrgs() {
        String sql = "SELECT id, trino_schema, password_encrypted "
                + "FROM data_sources WHERE source_type = 'tally' AND is_active = true";
        List<TallyOrgConnection> result = new ArrayList<>();
        try (Connection conn = connect();
                Statement stmt = conn.createStatement();
                ResultSet rs = stmt.executeQuery(sql)) {
            while (rs.next()) {
                result.add(toOrgConnection(rs));
            }
        } catch (SQLException e) {
            throw new RuntimeException("failed to list registered Tally connections", e);
        }
        return result;
    }

    public Optional<TallyOrgConnection> getOrgBySchema(String schemaName) {
        String sql = "SELECT id, trino_schema, password_encrypted "
                + "FROM data_sources WHERE source_type = 'tally' AND is_active = true AND trino_schema = ?";
        try (Connection conn = connect();
                PreparedStatement stmt = conn.prepareStatement(sql)) {
            stmt.setString(1, schemaName);
            try (ResultSet rs = stmt.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(toOrgConnection(rs));
                }
            }
        } catch (SQLException e) {
            throw new RuntimeException("failed to look up Tally connection for schema " + schemaName, e);
        }
        return Optional.empty();
    }

    private Connection connect() throws SQLException {
        return DriverManager.getConnection(jdbcUrl, jdbcUser, jdbcPassword);
    }

    private TallyOrgConnection toOrgConnection(ResultSet rs) throws SQLException {
        int id = rs.getInt("id");
        String schemaName = rs.getString("trino_schema");
        String bridgeToken = crypto.decrypt(rs.getString("password_encrypted"));
        return new TallyOrgConnection(id, schemaName, bridgeToken);
    }
}
