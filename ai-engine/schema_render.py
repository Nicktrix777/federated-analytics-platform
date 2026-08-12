"""
Schema rendering for deeply-nested Trino types — SCHEMA-AGNOSTIC.

The catalog stores each column's full Trino type in `data_type`, and for
document sources (Elasticsearch, MongoDB) that type is often a deeply nested
`ROW(...)` / `ARRAY(ROW(...))` blob, e.g.

    details  ROW(ATTACHMENTS ROW(HASH VARCHAR, URL VARCHAR), CONSTRAINTS ROW(...), ...)

Dumping that blob verbatim into a prompt (the old behavior) forces the LLM to
guess how to navigate it, which produces the two failure modes we saw against
Trino:
  - "Expression <col> is not of type ROW"  — dot-dereferencing a path that
    isn't actually a struct, or the reverse.
  - "Cannot unnest type: row(...)"          — UNNEST on a ROW (only ARRAY can
    be unnested); a plural-sounding field like `transactions` is often a single
    ROW, not an array.
  - "Column alias list has N entries..."     — wrong alias arity when unnesting
    an ARRAY(ROW).

This module parses ANY Trino type string into a tree and renders each column as
explicit, copy-pasteable access paths:
  - ROW      → dotted leaf paths           (details.attachments.url (varchar))
  - ARRAY(ROW) → an UNNEST recipe with the field list in order
  - ARRAY(scalar) / MAP → a one-line note

Nothing here is specific to any one index — it works off the type grammar, so a
new data source with a different nested shape renders the same way.
"""

import json
from collections import deque
from typing import Optional


# ── Type-string parser ────────────────────────────────────────────
# Trino renders composite types as ROW(name type, ...), ARRAY(type),
# MAP(keytype, valuetype); scalars are printed as-is (VARCHAR, BIGINT,
# TIMESTAMP(3), DECIMAL(10,2), ...). The parser only needs to respect nested
# parentheses to split correctly.

def _split_top_level(inner: str) -> list[str]:
    """Split on commas that sit at parenthesis-depth 0."""
    parts, depth, buf = [], 0, []
    for ch in inner:
        if ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _split_field(field: str) -> tuple[Optional[str], str]:
    """Split one ROW field 'name type' into (name, type).

    Handles quoted names ("@ts" varchar) and anonymous fields (type only).
    """
    field = field.strip()
    if not field:
        return None, field
    if field[0] == '"':
        end = field.find('"', 1)
        if end != -1:
            name = field[: end + 1]  # keep the quotes — they're part of the identifier
            rest = field[end + 1 :].strip()
            return (name, rest) if rest else (None, field)
    # First whitespace-delimited token is the field name; the rest is its type.
    i = 0
    while i < len(field) and not field[i].isspace():
        i += 1
    if i >= len(field):
        return None, field  # anonymous (bare type, no field name)
    name, rest = field[:i], field[i:].strip()
    # Guard: an unbalanced '(' in the "name" means we split inside a type like
    # DECIMAL(10, 2) that had no field name — treat the whole thing as a type.
    if name.count("(") != name.count(")"):
        return None, field
    return name, rest


def parse_trino_type(type_str: str) -> dict:
    """Parse a Trino type string into a tree of {kind, ...} nodes.

    kinds: 'row' {fields:[(name,node)]}, 'array' {element:node},
           'map' {key,value}, 'scalar' {type:str}.
    """
    s = (type_str or "").strip()
    up = s.upper()
    for kw, kind in (("ROW", "row"), ("ARRAY", "array"), ("MAP", "map")):
        if up.startswith(kw) and s[len(kw) :].lstrip().startswith("(") and s.endswith(")"):
            inner = s[s.find("(") + 1 : -1]
            if kind == "row":
                fields = []
                for f in _split_top_level(inner):
                    name, ftype = _split_field(f)
                    fields.append((name, parse_trino_type(ftype)))
                return {"kind": "row", "fields": fields}
            if kind == "array":
                return {"kind": "array", "element": parse_trino_type(inner)}
            parts = _split_top_level(inner)  # map
            return {
                "kind": "map",
                "key": parse_trino_type(parts[0]) if parts else {"kind": "scalar", "type": "varchar"},
                "value": parse_trino_type(parts[1]) if len(parts) > 1 else {"kind": "scalar", "type": "unknown"},
            }
    return {"kind": "scalar", "type": s}


def _type_summary(node: dict) -> str:
    """Short one-token-ish label for a node (used inline, not for navigation)."""
    kind = node["kind"]
    if kind == "scalar":
        return node["type"].lower()
    if kind == "array":
        return f"array({_type_summary(node['element'])})"
    if kind == "map":
        return f"map({_type_summary(node['key'])}→{_type_summary(node['value'])})"
    return "row"


def is_nested(data_type: str) -> bool:
    return parse_trino_type(data_type)["kind"] != "scalar"


def summarize_type(data_type: str) -> str:
    """Short label for a column's type: the scalar name, or 'row' / 'array(row)' / 'map(...)'."""
    return _type_summary(parse_trino_type(data_type))


# ── Rendering ─────────────────────────────────────────────────────
#
# No real customer value is ever rendered here — only derived, non-literal
# classifications (a detected pattern/semantic type). Real sampled values are
# used transiently in ai-engine/profiler.py to compute these, then discarded;
# nothing literal is persisted or shown downstream of that.

def parse_leaf_stats(raw) -> dict:
    """Parse a column_profiles.stats cell into {leaf_path: {pattern, semantic_type}}.

    Written by the profiler for NESTED leaf paths only (a column's own
    top-level pattern/semantic_type live in their own dedicated columns).
    Anything else (empty/malformed) yields no hints rather than guessing.
    """
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        return {}


def _leaf_format_hint(leaf_stats: Optional[dict], path: str) -> str:
    """`[format: X]` suffix for a nested leaf path, from its derived pattern/semantic
    type — never a literal example value."""
    entry = (leaf_stats or {}).get(path)
    if not entry:
        return ""
    label = entry.get("semantic_type") or entry.get("pattern")
    return f" [format: {label}]" if label else ""


def describe_column(
    column_name: str,
    data_type: str,
    leaf_stats: Optional[dict] = None,
    *,
    max_depth: int = 6,
    max_leaves: int = 40,
) -> tuple[str, list[str]]:
    """Render a column for a prompt's schema context.

    `leaf_stats` is the parsed {full_leaf_path: {pattern, semantic_type}} map
    for this column (column_profiles.stats); when present, a `[format: X]`
    hint is appended to the matching leaf line — never a literal value.

    Returns (inline_type, detail_lines):
      - scalar column → (scalar type, []) so the caller can render it on one line.
      - nested column → (summary label, indented access paths) so the LLM sees
        exactly which dotted paths are valid and where an UNNEST is required.
    """
    node = parse_trino_type(data_type)
    if node["kind"] == "scalar":
        return node["type"].lower(), []

    leaves: list[str] = []
    state = {"count": 0, "truncated": False}

    def sfx(path: str) -> str:
        return _leaf_format_hint(leaf_stats, path)

    def add(line: str) -> None:
        if state["count"] >= max_leaves:
            state["truncated"] = True
            return
        leaves.append(line)
        state["count"] += 1

    # Breadth-first traversal: emit shallow fields before drilling into any one
    # deep subtree, so a huge sibling (e.g. a 100-leaf `drivers` object) can't
    # crowd shallow siblings (e.g. `expectedReturnDate`) out of the capped
    # output — depth-first did exactly that, and the model then hallucinated the
    # hidden field names. `disp` is the path shown (uses UNNEST aliases across
    # arrays); `canon` is the array-transparent dotted data path used to look up
    # leaf_stats (column.field.field). They diverge once an ARRAY(ROW) is unnested.
    queue: deque = deque()
    if node["kind"] == "row":
        for fn, child in node["fields"]:
            seg = fn or "?"
            queue.append((child, f"{column_name}.{seg}", f"{column_name}.{seg}", 1))
    else:  # array/map at the top level
        queue.append((node, column_name, column_name, 0))

    while queue:
        if state["count"] >= max_leaves:
            state["truncated"] = True
            break
        n, disp, canon, depth = queue.popleft()
        kind = n["kind"]
        if kind == "scalar":
            add(f"    - {disp} ({n['type'].lower()}){sfx(canon)}")
        elif kind == "map":
            add(f"    - {disp} ({_type_summary(n)}) — read with element_at({disp}, <key>)")
        elif kind == "array":
            el = n["element"]
            if el["kind"] == "row":
                # ARRAY(ROW): access ONLY via CROSS JOIN UNNEST in the FROM clause
                # (never dot-access, never UNNEST in the SELECT list). Trino names
                # the unnested columns after the row's fields, so a bare table
                # alias is enough — no need to list every field (which is what
                # caused the "alias list has N entries" failures).
                alias = _alias_for(disp)
                first = el["fields"][0][0] if el["fields"] else "field"
                add(
                    f"    - {disp} (array of row) — CROSS JOIN UNNEST({disp}) AS {alias}, "
                    f"then reference {alias}.<field> by name (e.g. {alias}.{first})"
                )
                if depth < max_depth:
                    for fn, child in el["fields"]:
                        seg = fn or "?"
                        queue.append((child, f"{alias}.{seg}", f"{canon}.{seg}", depth + 1))
                else:
                    state["truncated"] = True
            else:
                add(
                    f"    - {disp} ({_type_summary(n)}) — an array of scalars; "
                    f"filter with contains({disp}, <value>) or CROSS JOIN UNNEST{sfx(canon)}"
                )
        elif kind == "row":
            # A plain ROW isn't itself a leaf — expand into its fields (dot paths).
            if depth >= max_depth:
                add(f"    - {disp} (row — nested further; expand only if needed)")
                state["truncated"] = True
            else:
                for fn, child in n["fields"]:
                    seg = fn or "?"
                    queue.append((child, f"{disp}.{seg}", f"{canon}.{seg}", depth + 1))

    if state["truncated"]:
        leaves.append("    - … (more nested fields; ask for a narrower slice if needed)")

    return _type_summary(node), leaves


def _alias_for(path: str) -> str:
    """A readable UNNEST alias derived from the array's path (last segment)."""
    seg = path.replace('"', "").split(".")[-1] or "u"
    return f"{seg}_u"


def leaf_paths(
    column_name: str,
    data_type: str,
    *,
    max_depth: int = 6,
    max_leaves: int = 60,
) -> list[str]:
    """Flat list of dotted leaf-field paths (names only) for a column.

    Used to enrich schema embeddings so a question about a deeply nested field
    ("borrower nationality") can still retrieve the dataset that holds it.
    Returns [] for a plain scalar column.
    """
    node = parse_trino_type(data_type)
    if node["kind"] == "scalar":
        return []
    out: list[str] = []

    def walk(n: dict, path: str, depth: int) -> None:
        if len(out) >= max_leaves:
            return
        kind = n["kind"]
        if kind == "row" and depth < max_depth:
            for fn, child in n["fields"]:
                walk(child, f"{path}.{fn}" if fn else path, depth + 1)
        elif kind == "array":
            walk(n["element"], path, depth + 1)
        elif kind == "map":
            return
        else:
            out.append(path)

    if node["kind"] == "row":
        for fn, child in node["fields"]:
            walk(child, f"{column_name}.{fn}" if fn else column_name, 1)
    else:
        walk(node, column_name, 0)
    return out
