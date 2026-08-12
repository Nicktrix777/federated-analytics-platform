package com.federatedanalytics.trino.tally;

import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.xml.sax.InputSource;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import java.io.StringReader;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Extracts rows from a Tally XML export response for one entity - tag-driven
 * (finds every element matching the entity's xmlTag anywhere in the
 * document, tolerant of nesting) rather than assuming one rigid structure,
 * so it degrades gracefully (a missing field comes back null) instead of
 * throwing if a real Tally's exact output shape differs from what
 * TallyEntity assumes. See TallyEntity's class comment for the larger
 * caveat this connector needs live-Tally verification.
 */
final class TallyXmlParser {
    private TallyXmlParser() {
    }

    /** `columns` is the resolved set for this table - the fixed baseline plus
     * any live-discovered fields (see TallyColumnResolver) - not necessarily
     * just entity.columns(), so a query against a newly-discovered column
     * extracts correctly too. */
    static List<Map<String, Object>> rows(String xml, TallyEntity entity, List<ColumnDef> columns) {
        Document doc = parse(xml);
        NodeList matches = doc.getElementsByTagName(entity.xmlTag());
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int i = 0; i < matches.getLength(); i++) {
            Element element = (Element) matches.item(i);
            Map<String, Object> row = new LinkedHashMap<>();
            for (ColumnDef col : columns) {
                row.put(col.name(), extract(element, col));
            }
            rows.add(row);
        }
        return rows;
    }

    private static Object extract(Element element, ColumnDef col) {
        String tagName = col.name().toUpperCase();
        String text = col.isAttribute()
                ? element.getAttribute("NAME")
                : firstChildText(element, tagName.replace("_", ""));
        if ((text == null || text.isBlank()) && !col.isAttribute() && col.name().contains("_")) {
            // Discovered nested columns are named "<listPrefix>_<leafTag>" for
            // readability (see TallySchemaSampler), but getElementsByTagName
            // already searches the whole subtree regardless of depth - the
            // leaf tag alone is enough to find it wherever it actually lives,
            // ignoring the prefix for lookup. Also helps a FIXED column whose
            // real tag turns out to live one level deeper than assumed.
            String leaf = tagName.substring(tagName.lastIndexOf('_') + 1).replace("_", "");
            text = firstChildText(element, leaf);
        }
        if (text == null || text.isBlank()) {
            return null;
        }
        if (col.typeName().equals("double")) {
            try {
                // Tally often renders negative amounts with a trailing/leading sign or extra
                // whitespace/commas — strip anything that isn't part of a plain decimal number.
                return Double.parseDouble(text.replaceAll("[^0-9.\\-]", ""));
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return text.trim();
    }

    private static String firstChildText(Element parent, String tagName) {
        NodeList children = parent.getElementsByTagName(tagName);
        if (children.getLength() == 0) {
            return null;
        }
        Node node = children.item(0);
        return node.getTextContent();
    }

    private static Document parse(String xml) {
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            // Tally's export is a fixed, internally-generated document, not
            // untrusted user input reaching this parser from the open
            // internet - but disable external entity resolution anyway as
            // a cheap, standard XXE hardening default.
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            DocumentBuilder builder = factory.newDocumentBuilder();
            return builder.parse(new InputSource(new StringReader(xml)));
        } catch (Exception e) {
            throw new RuntimeException("failed to parse Tally XML response", e);
        }
    }
}
