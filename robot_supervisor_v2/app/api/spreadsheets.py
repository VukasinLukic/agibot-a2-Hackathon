from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime
from html import escape
from typing import Any, List, Tuple


SpreadsheetRows = List[List[Any]]
SpreadsheetSheets = List[Tuple[str, SpreadsheetRows]]


def build_xlsx(sheets: SpreadsheetSheets) -> bytes:
    """Build a small XLSX workbook without adding a runtime dependency."""
    if not sheets:
        raise ValueError("At least one sheet is required.")

    shared_strings, shared_string_ids = _shared_strings(sheets)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(sheets)))
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("xl/workbook.xml", _workbook_xml(sheets))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        zf.writestr("xl/styles.xml", _styles_xml())
        zf.writestr("xl/sharedStrings.xml", _shared_strings_xml(shared_strings))
        for index, (_, rows) in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows, shared_string_ids))

    return output.getvalue()


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
    candidate = base
    suffix = 2
    while candidate in used:
        trimmed = base[: max(1, 31 - len(str(suffix)) - 1)]
        candidate = f"{trimmed} {suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _column_name(column_index: int) -> str:
    letters = ""
    number = column_index
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _cell_ref(row_index: int, column_index: int) -> str:
    letters = _column_name(column_index)
    return f"{letters}{row_index}"


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return " | ".join(_string_value(item) for item in value)
    return str(value)


def _shared_strings(sheets: SpreadsheetSheets) -> tuple[list[str], dict[str, int]]:
    values: list[str] = []
    ids: dict[str, int] = {}
    for _, rows in sheets:
        for row in rows:
            for value in row:
                text = _string_value(value)
                if text not in ids:
                    ids[text] = len(values)
                    values.append(text)
    return values, ids


def _worksheet_xml(rows: SpreadsheetRows, shared_string_ids: dict[str, int]) -> str:
    max_columns = max((len(row) for row in rows), default=1)
    max_rows = max(len(rows), 1)
    dimension_ref = f"A1:{_column_name(max_columns)}{max_rows}"
    column_widths = []
    for column_index in range(max_columns):
        width = max(
            (len(_string_value(row[column_index])) for row in rows if column_index < len(row)),
            default=8,
        )
        column_widths.append(min(max(width + 2, 10), 60))

    cols_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(column_widths, start=1)
    )

    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            style = ' s="1"' if row_index == 1 else ""
            shared_string_id = shared_string_ids[_string_value(value)]
            cells.append(
                f'<c r="{_cell_ref(row_index, column_index)}" t="s"{style}>'
                f"<v>{shared_string_id}</v></c>"
            )
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    freeze = '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension_ref}"/>'
        f"<sheetViews><sheetView workbookViewId=\"0\">{freeze}</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{cols_xml}</cols>"
        f"<sheetData>{''.join(row_xml)}</sheetData>"
        f'<autoFilter ref="{dimension_ref}"/>'
        "</worksheet>"
    )


def _workbook_xml(sheets: SpreadsheetSheets) -> str:
    used_names: set[str] = set()
    sheet_xml = "".join(
        f'<sheet name="{escape(_safe_sheet_name(name, used_names))}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _) in enumerate(sheets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheet_xml}</sheets>"
        "</workbook>"
    )


def _workbook_rels(sheet_count: int) -> str:
    rels = "".join(
        '<Relationship '
        f'Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    rels += (
        '<Relationship Id="rIdStyles" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '<Relationship Id="rIdSharedStrings" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _content_types(sheet_count: int) -> str:
    sheets = "".join(
        '<Override '
        f'PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        f"{sheets}</Types>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<fonts count=\"2\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font>"
        "<font><b/><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts>"
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        "</styleSheet>"
    )


def _shared_strings_xml(values: list[str]) -> str:
    items = "".join(f"<si><t>{escape(value)}</t></si>" for value in values)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(values)}" uniqueCount="{len(values)}">'
        f"{items}</sst>"
    )
