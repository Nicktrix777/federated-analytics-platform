package com.federatedanalytics.trino.zohobooks;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Calls Zoho Books' REST API live - nothing here is ever persisted past a
 * single query. Access tokens are minted fresh from the stored refresh
 * token for every scan rather than cached, since Zoho's access tokens are
 * short-lived (~1 hour) and splits are infrequent enough that the extra
 * round trip is cheap next to the query itself.
 */
public class ZohoApiClient {
    private static final int PAGE_SIZE = 200;

    private final HttpClient httpClient = HttpClient.newHttpClient();
    private final ObjectMapper mapper = new ObjectMapper();

    public String mintAccessToken(ZohoOrgConnection org) {
        String url = "https://accounts.zoho." + org.dataCenter() + "/oauth/v2/token";
        String form = "grant_type=refresh_token"
                + "&client_id=" + urlEncode(org.clientId())
                + "&client_secret=" + urlEncode(org.clientSecret())
                + "&refresh_token=" + urlEncode(org.refreshToken());
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(form))
                .build();
        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            JsonNode json = mapper.readTree(response.body());
            String accessToken = json.path("access_token").asText(null);
            if (accessToken == null) {
                throw new RuntimeException("Zoho access token refresh failed: " + response.body());
            }
            return accessToken;
        } catch (IOException e) {
            throw new RuntimeException("failed to reach Zoho's OAuth token endpoint", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted while minting a Zoho access token", e);
        }
    }

    /** Fetches every page of the given entity's list endpoint, keyed by column name.
     * `columns` is the resolved set for this table - the fixed baseline plus any
     * live-discovered fields (see ZohoColumnResolver) - not necessarily just
     * entity.columns(), so a query against a newly-discovered column extracts
     * correctly too. */
    public List<Map<String, Object>> fetchAll(ZohoOrgConnection org, ZohoEntity entity, String accessToken, List<ColumnDef> columns) {
        List<Map<String, Object>> rows = new ArrayList<>();
        int page = 1;
        boolean hasMore = true;
        while (hasMore) {
            String url = "https://www.zohoapis." + org.dataCenter() + "/books/v3/" + entity.apiPath()
                    + "?organization_id=" + urlEncode(org.organizationId())
                    + "&page=" + page + "&per_page=" + PAGE_SIZE;
            HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                    .header("Authorization", "Zoho-oauthtoken " + accessToken)
                    .GET()
                    .build();
            try {
                HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
                if (response.statusCode() != 200) {
                    throw new RuntimeException(
                            "Zoho Books API request failed (" + response.statusCode() + "): " + response.body());
                }
                JsonNode json = mapper.readTree(response.body());
                for (JsonNode item : json.path(entity.responseArrayKey())) {
                    rows.add(toRow(columns, item));
                }
                hasMore = json.path("page_context").path("has_more_page").asBoolean(false);
                page++;
            } catch (IOException e) {
                throw new RuntimeException("failed to fetch Zoho " + entity.apiPath(), e);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new RuntimeException("interrupted while fetching Zoho " + entity.apiPath(), e);
            }
        }
        return rows;
    }

    /** One page, capped at `limit`, regardless of has_more_page - used only for
     * schema discovery (ZohoSchemaSampler), never for a real query's data. */
    public List<JsonNode> fetchSample(ZohoOrgConnection org, ZohoEntity entity, String accessToken, int limit) {
        String url = "https://www.zohoapis." + org.dataCenter() + "/books/v3/" + entity.apiPath()
                + "?organization_id=" + urlEncode(org.organizationId())
                + "&page=1&per_page=" + limit;
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .header("Authorization", "Zoho-oauthtoken " + accessToken)
                .GET()
                .build();
        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                throw new RuntimeException(
                        "Zoho Books API request failed (" + response.statusCode() + "): " + response.body());
            }
            JsonNode json = mapper.readTree(response.body());
            List<JsonNode> items = new ArrayList<>();
            for (JsonNode item : json.path(entity.responseArrayKey())) {
                items.add(item);
            }
            return items;
        } catch (IOException e) {
            throw new RuntimeException("failed to fetch a Zoho " + entity.apiPath() + " sample", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("interrupted while fetching a Zoho " + entity.apiPath() + " sample", e);
        }
    }

    private Map<String, Object> toRow(List<ColumnDef> columns, JsonNode item) {
        Map<String, Object> row = new LinkedHashMap<>();
        for (ColumnDef col : columns) {
            JsonNode field = item.path(col.name());
            if ((field.isMissingNode() || field.isNull()) && col.name().indexOf('_') > 0) {
                // A discovered nested field is named "<parent>_<child>" for
                // readability (see ZohoSchemaSampler) - the real JSON has it
                // one level down, not as a literal top-level key.
                int split = col.name().indexOf('_');
                field = item.path(col.name().substring(0, split)).path(col.name().substring(split + 1));
            }
            if (field.isMissingNode() || field.isNull()) {
                row.put(col.name(), null);
            } else if (col.typeName().equals("double")) {
                row.put(col.name(), field.asDouble());
            } else {
                row.put(col.name(), field.asText());
            }
        }
        return row;
    }

    private static String urlEncode(String value) {
        return URLEncoder.encode(value == null ? "" : value, StandardCharsets.UTF_8);
    }
}
