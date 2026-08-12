package com.federatedanalytics.trino.tally;

import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.xml.sax.InputSource;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import java.io.StringReader;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Live-samples a real Tally response to discover columns beyond
 * TallyEntity's fixed baseline - e.g. a real ledger's closing balance lives
 * inside a nested {@code <LEDGERCLOSINGVALUES.LIST>} the fixed schema never
 * modeled (confirmed against a real TallyPrime export). Only field NAMES and
 * an inferred TYPE are ever returned; the actual sampled text is read into
 * local variables purely to classify (parseable as a number? every value
 * exactly "Yes"/"No"?) and goes out of scope immediately after - nothing
 * here is written anywhere, logged, or returned past this class.
 */
final class TallySchemaSampler {
    // How many top-level records (e.g. ledgers) to look at before deciding a
    // field's shape - one record can't distinguish a real boolean-like field
    // from a coincidental "No" value; Tally's export has no pagination, so
    // this just reads fewer of the rows the response already contains.
    private static final int SAMPLE_SIZE = 5;

    // Tally bookkeeping fields that are real but never useful to query.
    private static final Set<String> SKIP_TAGS = Set.of(
            "GUID", "ALTERID", "OBJECTUPDATEACTION", "UPDATEDDATETIME", "SORTPOSITION");

    private final TallyTransport transport;
    private final Duration timeout;

    TallySchemaSampler(TallyTransport transport, Duration timeout) {
        this.transport = transport;
        this.timeout = timeout;
    }

    /** entity.columns() plus any newly-discovered fields, from one live sample. */
    List<ColumnDef> discover(int dataSourceId, TallyEntity entity) {
        String xml = transport.send(dataSourceId, entity.buildRequestXml(), timeout);
        Document doc = parse(xml);
        NodeList matches = doc.getElementsByTagName(entity.xmlTag());

        Map<String, ColumnDef> merged = new LinkedHashMap<>();
        // Tracked by the ACTUAL XML tag a fixed column resolves to (see
        // TallyXmlParser.extract's squish rule), not by column-name string
        // equality - a raw tag like CLOSINGBALANCE lowercases to
        // "closingbalance" during sampling, which is a different map key
        // than the fixed column's own "closing_balance" even though both
        // name the exact same field. Comparing by column name alone would
        // "discover" every fixed field a second time under its raw spelling.
        Set<String> knownTags = new HashSet<>();
        for (ColumnDef col : entity.columns()) {
            merged.put(col.name(), col);
            knownTags.add(col.name().toUpperCase().replace("_", ""));
        }

        Map<String, List<String>> observed = new LinkedHashMap<>();
        int sampleCount = Math.min(matches.getLength(), SAMPLE_SIZE);
        for (int i = 0; i < sampleCount; i++) {
            walk((Element) matches.item(i), "", observed);
        }
        for (Map.Entry<String, List<String>> entry : observed.entrySet()) {
            String columnName = entry.getKey();
            if (knownTags.contains(columnName.toUpperCase().replace("_", ""))) {
                continue; // same field the fixed baseline already covers, just a different spelling
            }
            classify(columnName, entry.getValue()).ifPresent(col -> merged.put(col.name(), col));
        }
        return new ArrayList<>(merged.values());
    }

    /** Walks one element's children, flattening exactly one level of nested
     * {@code .LIST} wrappers (e.g. LEDGERCLOSINGVALUES.LIST -&gt;
     * ledgerclosingvalues_closingbalance) - the shape that hides real fields
     * like a ledger's closing balance from a flat-tag-only reading. */
    private void walk(Element element, String prefix, Map<String, List<String>> observed) {
        NodeList children = element.getChildNodes();
        for (int i = 0; i < children.getLength(); i++) {
            if (!(children.item(i) instanceof Element child)) {
                continue;
            }
            String tag = child.getTagName();
            if (SKIP_TAGS.contains(tag)) {
                continue;
            }
            if (tag.endsWith(".LIST")) {
                if (prefix.isEmpty()) { // only flatten one level deep for this MVP
                    String nestedPrefix = tag.substring(0, tag.length() - ".LIST".length()).toLowerCase();
                    walk(child, nestedPrefix, observed);
                }
                continue;
            }
            String columnName = prefix.isEmpty() ? tag.toLowerCase() : prefix + "_" + tag.toLowerCase();
            String text = child.getTextContent();
            if (text != null && !text.isBlank()) {
                observed.computeIfAbsent(columnName, k -> new ArrayList<>()).add(text.trim());
            }
        }
    }

    /** Infers a column from its observed values across the sample, or nothing if
     * every value is an administrative Yes/No flag - real, but not useful to
     * query. Detected by VALUE, not a hardcoded name list, so this generalizes
     * to fields never seen before (confirmed against a real export: ~50 such
     * flags on one ledger record, e.g. ISBILLWISEON). */
    private Optional<ColumnDef> classify(String columnName, List<String> values) {
        boolean allBoolean = values.stream().allMatch(v -> v.equals("Yes") || v.equals("No"));
        if (allBoolean) {
            return Optional.empty();
        }
        boolean allNumeric = values.stream().allMatch(TallySchemaSampler::looksNumeric);
        return Optional.of(new ColumnDef(columnName, allNumeric ? "double" : "varchar"));
    }

    private static boolean looksNumeric(String s) {
        String stripped = s.replaceAll("[^0-9.\\-]", "");
        if (stripped.isEmpty()) {
            return false;
        }
        try {
            Double.parseDouble(stripped);
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }

    private static Document parse(String xml) {
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            // Same XXE hardening as TallyXmlParser - Tally's export is a fixed,
            // internally-generated document, not untrusted input, but disabling
            // external entity resolution is a cheap standard default anyway.
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            DocumentBuilder builder = factory.newDocumentBuilder();
            return builder.parse(new InputSource(new StringReader(xml)));
        } catch (Exception e) {
            throw new RuntimeException("failed to parse Tally XML response for schema discovery", e);
        }
    }
}
