package com.federatedanalytics.trino.tally;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;

/**
 * Sends one Tally XML request through core-api's tally-bridge tunnel and
 * returns the raw XML response. core-api's forward endpoint blocks until
 * the customer's bridge relays a response (or times out), so this is a
 * plain synchronous HTTP call from the plugin's point of view - the
 * tunneling happens entirely inside core-api.
 */
public class TallyTransport {
    private final HttpClient httpClient = HttpClient.newHttpClient();
    private final String coreApiBaseUrl;
    private final String internalServiceToken;

    public TallyTransport(String coreApiBaseUrl, String internalServiceToken) {
        this.coreApiBaseUrl = coreApiBaseUrl;
        this.internalServiceToken = internalServiceToken;
    }

    public String send(int dataSourceId, String requestXml) {
        return send(dataSourceId, requestXml, Duration.ofSeconds(35)); // longer than core-api's own 30s forward timeout
    }

    /** Overload with an explicit timeout - used by schema discovery, which runs on
     * the much hotter metadata-call path and needs to fail fast rather than block
     * for as long as a real per-scan data fetch is allowed to. */
    public String send(int dataSourceId, String requestXml, Duration timeout) {
        String url = coreApiBaseUrl + "/internal/tally-tunnel/" + dataSourceId + "/forward";
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .timeout(timeout)
                .header("X-Internal-Service-Token", internalServiceToken)
                .header("Content-Type", "application/xml")
                .POST(HttpRequest.BodyPublishers.ofString(requestXml))
                .build();
        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                throw new RuntimeException(
                        "tally-bridge tunnel request failed (" + response.statusCode() + "): " + response.body());
            }
            return response.body();
        } catch (IOException e) {
            throw new RuntimeException("failed to reach core-api's tally-bridge tunnel", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted while forwarding a request through the tally-bridge tunnel", e);
        }
    }
}
