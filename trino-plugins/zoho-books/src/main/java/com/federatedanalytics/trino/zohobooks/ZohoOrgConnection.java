package com.federatedanalytics.trino.zohobooks;

/** One registered customer's Zoho Books connection, resolved from data_sources. */
public record ZohoOrgConnection(
        int dataSourceId,
        String schemaName,
        String clientId,
        String clientSecret,
        String refreshToken,
        String dataCenter,
        String organizationId) {
}
