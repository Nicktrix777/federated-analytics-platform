package com.federatedanalytics.trino.zohobooks;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

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
 * Reads registered zoho_books data_sources rows directly from postgres-meta
 * and decrypts their credentials - each row is one customer's Zoho org,
 * distinguished by trino_schema, sharing the one static zoho_books catalog.
 * A plain per-call connection (no pooling) is deliberate: metadata lookups
 * happen once per query planning pass, not per row, so the extra connection
 * overhead is negligible against the win of not needing a pooling library.
 */
public class MetadataStore {
    private static final ObjectMapper JSON = new ObjectMapper();

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

    public List<ZohoOrgConnection> listOrgs() {
        String sql = "SELECT id, trino_schema, password_encrypted, extra_config::text "
                + "FROM data_sources WHERE source_type = 'zoho_books' AND is_active = true";
        List<ZohoOrgConnection> result = new ArrayList<>();
        try (Connection conn = connect();
                Statement stmt = conn.createStatement();
                ResultSet rs = stmt.executeQuery(sql)) {
            while (rs.next()) {
                result.add(toOrgConnection(rs));
            }
        } catch (SQLException e) {
            throw new RuntimeException("failed to list registered Zoho Books connections", e);
        }
        return result;
    }

    public Optional<ZohoOrgConnection> getOrgBySchema(String schemaName) {
        String sql = "SELECT id, trino_schema, password_encrypted, extra_config::text "
                + "FROM data_sources WHERE source_type = 'zoho_books' AND is_active = true AND trino_schema = ?";
        try (Connection conn = connect();
                PreparedStatement stmt = conn.prepareStatement(sql)) {
            stmt.setString(1, schemaName);
            try (ResultSet rs = stmt.executeQuery()) {
                if (rs.next()) {
                    return Optional.of(toOrgConnection(rs));
                }
            }
        } catch (SQLException e) {
            throw new RuntimeException("failed to look up Zoho Books connection for schema " + schemaName, e);
        }
        return Optional.empty();
    }

    private Connection connect() throws SQLException {
        return DriverManager.getConnection(jdbcUrl, jdbcUser, jdbcPassword);
    }

    private ZohoOrgConnection toOrgConnection(ResultSet rs) throws SQLException {
        int id = rs.getInt("id");
        String schemaName = rs.getString("trino_schema");
        String credsJson = crypto.decrypt(rs.getString("password_encrypted"));
        String extraConfigJson = rs.getString("extra_config");
        try {
            JsonNode creds = JSON.readTree(credsJson);
            JsonNode extra = (extraConfigJson == null || extraConfigJson.isBlank())
                    ? JSON.createObjectNode()
                    : JSON.readTree(extraConfigJson);
            return new ZohoOrgConnection(
                    id,
                    schemaName,
                    creds.path("client_id").asText(),
                    creds.path("client_secret").asText(),
                    creds.path("refresh_token").asText(),
                    extra.path("data_center").asText("com"),
                    extra.path("organization_id").asText());
        } catch (Exception e) {
            throw new RuntimeException("failed to parse stored credentials for data_sources.id=" + id, e);
        }
    }
}
