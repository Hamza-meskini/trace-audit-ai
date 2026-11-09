"""Merged cells and stacked headers survive table parsing (UND-08)."""

from pathlib import Path

from app.services import databricks_document_ai as dda
from app.services import extraction
from app.services.ingestion import build_structure_aware_chunks


# Two header rows (a 'Limits' group over Min/Max) and a requirement cell
# merged down over two condition rows.
MERGED_TABLE = """
<table>
  <thead>
    <tr><th rowspan="2">Identifier</th><th rowspan="2">Condition</th><th colspan="2">Limits</th><th rowspan="2">Unit</th></tr>
    <tr><th>Min</th><th>Max</th></tr>
  </thead>
  <tbody>
    <tr><td rowspan="2">REQ-A-010</td><td>Supply voltage shall stay within limits</td><td>9</td><td>16</td><td>V</td></tr>
    <tr><td>Ripple shall stay within limits</td><td>0</td><td>0.2</td><td>V</td></tr>
    <tr><td>REQ-A-011</td><td colspan="3">Startup shall complete within 2 s</td><td>s</td></tr>
  </tbody>
</table>
"""


def parse():
    table = dda._parse_table(MERGED_TABLE)
    assert table is not None
    return table


def test_spans_are_expanded_into_a_rectangular_grid():
    table = parse()
    assert table.header_rows == 2
    assert table.merged_cells == 6
    assert table.rows == [
        ["Identifier", "Condition", "Limits", "Limits", "Unit"],
        ["Identifier", "Condition", "Min", "Max", "Unit"],
        ["REQ-A-010", "Supply voltage shall stay within limits", "9", "16", "V"],
        ["REQ-A-010", "Ripple shall stay within limits", "0", "0.2", "V"],
        ["REQ-A-011", "Startup shall complete within 2 s", "Startup shall complete within 2 s",
         "Startup shall complete within 2 s", "s"],
    ]


def test_header_path_names_every_column():
    table = parse()
    assert dda.table_header_path(table.rows, table.header_rows) == [
        "Identifier", "Condition", "Limits > Min", "Limits > Max", "Unit",
    ]


def test_simple_tables_are_unchanged():
    simple = "<table><tr><td>A</td><td></td></tr><tr><td>1</td><td>2</td></tr></table>"
    table = dda._parse_table(simple)
    assert table.rows == [["A", ""], ["1", "2"]]
    assert table.header_rows == 0 and table.merged_cells == 0


def adapted_chunks():
    result = {"document": {
        "pages": [{"id": 0}],
        "elements": [
            {"id": 0, "type": "section_header", "content": "Electrical limits",
             "bbox": [{"page_id": 0, "coord": [0, 0, 10, 10]}]},
            {"id": 1, "type": "table", "content": MERGED_TABLE,
             "bbox": [{"page_id": 0, "coord": [0, 20, 10, 30]}]},
        ],
    }}
    elements, _pages, _diag = dda._adapt_parse_result(
        result, file_path="table.docx", source_sha256="x", cache_path=Path("x"), cache_hit=True,
    )
    return build_structure_aware_chunks(elements, source_sha256="x", parser_backend="test")


def test_table_chunk_carries_header_paths_and_row_ids():
    table = next(chunk for chunk in adapted_chunks() if chunk.metadata["block_type"] == "table")
    meta = table.metadata["table"]
    assert meta["headers"] == ["Identifier", "Condition", "Limits > Min", "Limits > Max", "Unit"]
    assert meta["header_rows"] == 2 and meta["merged_cells"] == 6
    assert meta["row_requirement_ids"] == ["REQ-A-010", "REQ-A-010", "REQ-A-011"]
    assert "| Identifier | Condition | Limits > Min | Limits > Max | Unit |" in table.content


def test_a_requirement_merged_over_two_rows_is_one_source_block():
    text = "\n\n".join(chunk.content for chunk in adapted_chunks())
    duplicates = {}
    blocks = extraction._source_blocks_by_code(text, duplicates=duplicates)
    assert duplicates == {}
    assert sorted(blocks) == ["REQ-A-010", "REQ-A-011"]
    assert "Supply voltage" in blocks["REQ-A-010"] and "Ripple" in blocks["REQ-A-010"]
    assert blocks["REQ-A-010"].startswith("| Identifier | Condition | Limits > Min")
    discovered = extraction._targeted_discovery_from_source(blocks, {"REQ-A-010"})[0]
    assert "Supply voltage" in discovered.description and "Ripple" in discovered.description
