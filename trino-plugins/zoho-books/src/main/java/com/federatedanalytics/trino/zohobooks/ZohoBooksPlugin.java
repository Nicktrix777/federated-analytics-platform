package com.federatedanalytics.trino.zohobooks;

import io.trino.spi.Plugin;
import io.trino.spi.connector.ConnectorFactory;

import java.util.List;

public class ZohoBooksPlugin implements Plugin {
    @Override
    public Iterable<ConnectorFactory> getConnectorFactories() {
        return List.of(new ZohoBooksConnectorFactory());
    }
}
