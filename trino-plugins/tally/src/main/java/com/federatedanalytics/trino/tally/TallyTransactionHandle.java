package com.federatedanalytics.trino.tally;

import io.trino.spi.connector.ConnectorTransactionHandle;

/** Stateless connector, so a single shared marker instance is enough. */
public enum TallyTransactionHandle implements ConnectorTransactionHandle {
    INSTANCE
}
