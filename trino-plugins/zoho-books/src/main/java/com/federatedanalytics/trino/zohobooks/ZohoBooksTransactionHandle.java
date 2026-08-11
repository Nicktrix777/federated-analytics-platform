package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.connector.ConnectorTransactionHandle;

/** Stateless connector, so a single shared marker instance is enough. */
public enum ZohoBooksTransactionHandle implements ConnectorTransactionHandle {
    INSTANCE
}
