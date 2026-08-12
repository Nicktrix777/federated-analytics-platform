package com.federatedanalytics.trino.tally;

/**
 * One registered customer's Tally connection, resolved from data_sources.
 * bridgeToken is the decrypted secret tally-bridge itself authenticates
 * with — direct (non-bridged, same-network) connectivity is explicitly
 * deferred, so every registered tally datasource goes through the tunnel.
 */
public record TallyOrgConnection(int dataSourceId, String schemaName, String bridgeToken) {
}
