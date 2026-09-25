import html
import io
import json
import re
import textwrap
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal

from content_service.content_store.surveys.service import SurveyService
from .spreadsheets import build_xlsx

router = APIRouter(prefix="/api/surveys", tags=["surveys"])
survey_service = SurveyService(include_examples=False)
SURVEY_SNAPSHOTS_DIR = Path(__file__).resolve().parents[3] / "livekit-client" / "survey_snapshots"
SURVEY_EXPORT_PATH_LEGACY = Path(__file__).resolve().parents[3] / "livekit-client" / "survey_responses_debug.json"

class SurveyCreateRequest(BaseModel): 
    slug: str
    title: str 
    description: str = ""


class SurveyUpdateRequest(SurveyCreateRequest):
    pass

class SurveyQuestionCreateRequest(BaseModel):
    question_key: str 
    question: str 
    response_kind: Literal[
        "single_choice", 
        "multiple_choice", 
        "free_text", 
        "ranking"
    ]
    options: list[str] = Field(default_factory=list)
    allow_skip: bool = True
    order_index: int = 0


class SurveyQuestionUpdateRequest(SurveyQuestionCreateRequest):
    pass 

class SurveySelectionUpdateRequest(BaseModel):
    survey_slug: str


def _safe_filename(value: str, fallback: str = "survey") -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    return normalized or fallback


def _raise_survey_http_exception(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=500, detail=f"Survey operation failed: {str(exc)}")


def _resolve_latest_snapshot_path() -> Path | None:
    if SURVEY_SNAPSHOTS_DIR.is_dir():
        candidates = [
            f for f in SURVEY_SNAPSHOTS_DIR.iterdir()
            if f.suffix == ".json" and f.is_file()
        ]
        if candidates:
            return max(candidates, key=lambda f: f.stat().st_mtime)
    if SURVEY_EXPORT_PATH_LEGACY.exists():
        return SURVEY_EXPORT_PATH_LEGACY
    return None


def _load_latest_survey_snapshot() -> dict:
    path = _resolve_latest_snapshot_path()
    if path is None:
        raise LookupError("No survey result snapshot is available yet.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Survey result snapshot is not valid JSON.") from exc


def _latest_survey_snapshot_updated_at() -> float | None:
    path = _resolve_latest_snapshot_path()
    return path.stat().st_mtime if path is not None else None


def _build_export_status(snapshot: dict | None = None) -> dict:
    if snapshot is None:
        if _resolve_latest_snapshot_path() is None:
            return {
                "available": False,
                "reason": "No survey result snapshot is available yet.",
            }
        snapshot = _load_latest_survey_snapshot()

    phase = snapshot.get("phase")
    survey_id = snapshot.get("survey_id")
    questions = snapshot.get("questions") or []
    updated_at = _latest_survey_snapshot_updated_at()

    return {
        "available": phase == "finished",
        "survey_id": survey_id,
        "phase": phase,
        "total_questions": snapshot.get("total_questions", len(questions)),
        "responses_recorded": snapshot.get("responses_recorded", 0),
        "updated_at": updated_at,
        "reason": None if phase == "finished" else "Survey export is available after the survey is concluded.",
    }


def _get_response_text(response: dict) -> str:
    ranking = response.get("ranking") or []
    if ranking:
        return " > ".join(ranking)

    selected_options = response.get("selected_options") or []
    if selected_options:
        return " | ".join(selected_options)

    return response.get("free_text") or response.get("raw_input") or ""


def _get_export_rows(snapshot: dict) -> list[dict[str, str]]:
    rows = []
    for index, question in enumerate(snapshot.get("questions", []), start=1):
        response = question.get("response") or {}
        rows.append(
            {
                "number": str(index),
                "question_id": str(question.get("id", "")),
                "question": str(question.get("question", "")),
                "response_kind": str(question.get("response_kind", "")),
                "response": _get_response_text(response),
                "raw_input": str(response.get("raw_input", "")),
            }
        )
    return rows


def _snapshot_to_word_doc(snapshot: dict) -> str:
    rows = _get_export_rows(snapshot)
    table_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(row['number'])}</td>"
        f"<td>{html.escape(row['question'])}</td>"
        f"<td>{html.escape(row['response_kind'])}</td>"
        f"<td>{html.escape(row['response'])}</td>"
        "</tr>"
        for row in rows
    )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Survey Results</title>
  <style>
    body {{ font-family: Arial, sans-serif; font-size: 11pt; }}
    h1 {{ font-size: 18pt; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #999; padding: 6px; vertical-align: top; }}
    th {{ background: #eee; }}
  </style>
</head>
<body>
  <h1>Survey Results</h1>
  <p><strong>Survey:</strong> {html.escape(str(snapshot.get("survey_id", "")))}</p>
  <p><strong>Responses:</strong> {html.escape(str(snapshot.get("responses_recorded", 0)))} / {html.escape(str(snapshot.get("total_questions", len(rows))))}</p>
  <table>
    <thead>
      <tr><th>#</th><th>Question</th><th>Type</th><th>Response</th></tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>
</body>
</html>"""


def _runs_to_word_doc(runs: list[dict]) -> str:
    sections = []
    for index, run in enumerate(runs, start=1):
        snapshot = run["snapshot"]
        rows = _get_export_rows(snapshot)
        table_rows = "\n".join(
            "<tr>"
            f"<td>{html.escape(row['number'])}</td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{html.escape(row['response_kind'])}</td>"
            f"<td>{html.escape(row['response'])}</td>"
            "</tr>"
            for row in rows
        )
        sections.append(
            f"""
  <h2>Run {index}: {html.escape(str(run.get("survey_title") or run.get("survey_slug") or ""))}</h2>
  <p><strong>Saved:</strong> {html.escape(str(run.get("created_at") or "Unknown time"))}</p>
  <p><strong>Survey:</strong> {html.escape(str(run.get("survey_slug") or ""))}</p>
  <p><strong>Responses:</strong> {html.escape(str(run.get("responses_recorded", 0)))} / {html.escape(str(run.get("total_questions", len(rows))))}</p>
  <table>
    <thead>
      <tr><th>#</th><th>Question</th><th>Type</th><th>Response</th></tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>
"""
        )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Survey Results Archive</title>
  <style>
    body {{ font-family: Arial, sans-serif; font-size: 11pt; }}
    h1 {{ font-size: 20pt; }}
    h2 {{ font-size: 15pt; margin-top: 24px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 18px; }}
    th, td {{ border: 1px solid #999; padding: 6px; vertical-align: top; }}
    th {{ background: #eee; }}
  </style>
</head>
<body>
  <h1>Survey Results Archive</h1>
  <p><strong>Total saved runs:</strong> {len(runs)}</p>
  {''.join(sections)}
</body>
</html>"""


def _ascii_pdf_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _minimal_pdf(lines: list[str]) -> bytes:
    page_width, page_height = 595, 842
    left, top = 50, 790
    line_height = 14
    max_lines = 52
    pages = [lines[index : index + max_lines] for index in range(0, len(lines), max_lines)] or [[]]

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_refs = []

    def pdf_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    for page_lines in pages:
        stream_lines = ["BT", "/F1 10 Tf", f"{left} {top} Td", f"{line_height} TL"]
        for line in page_lines:
            stream_lines.append(f"({_ascii_pdf_text(pdf_escape(line))}) Tj")
            stream_lines.append("T*")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1", "ignore")
        content_obj_id = len(objects) + 1
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
        page_obj_id = len(objects) + 1
        page_refs.append(f"{page_obj_id} 0 R")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj_id} 0 R >>"
            ).encode("ascii")
        )

    objects[1] = f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>".encode("ascii")

    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n".encode("ascii"))
        output.write(obj)
        output.write(b"\nendobj\n")

    xref_offset = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return output.getvalue()


def _snapshot_to_pdf(snapshot: dict) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError:
        lines = [
            "Survey Results",
            f"Survey: {snapshot.get('survey_id', '')}",
            f"Responses: {snapshot.get('responses_recorded', 0)} / {snapshot.get('total_questions', 0)}",
            "",
        ]
        for row in _get_export_rows(snapshot):
            lines.extend(
                textwrap.wrap(
                    f"{row['number']}. {row['question']} [{row['response_kind']}] - {row['response']}",
                    width=92,
                )
            )
            lines.append("")
        return _minimal_pdf(lines)

    buffer = io.BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    font_name = "Helvetica"
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if font_path.exists():
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(font_path)))
        font_name = "DejaVuSans"

    y = page_height - 50
    pdf.setFont(font_name, 16)
    pdf.drawString(50, y, "Survey Results")
    y -= 28
    pdf.setFont(font_name, 10)
    pdf.drawString(50, y, f"Survey: {snapshot.get('survey_id', '')}")
    y -= 16
    pdf.drawString(
        50,
        y,
        f"Responses: {snapshot.get('responses_recorded', 0)} / {snapshot.get('total_questions', 0)}",
    )
    y -= 24

    for row in _get_export_rows(snapshot):
        wrapped_lines = textwrap.wrap(
            f"{row['number']}. {row['question']} [{row['response_kind']}] - {row['response']}",
            width=105,
        ) or [""]
        for line in wrapped_lines:
            if y < 50:
                pdf.showPage()
                pdf.setFont(font_name, 10)
                y = page_height - 50
            pdf.drawString(50, y, line)
            y -= 14
        y -= 8

    pdf.save()
    buffer.seek(0)
    return buffer.read()


def _runs_to_pdf(runs: list[dict]) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError:
        lines = ["Survey Results Archive", f"Total saved runs: {len(runs)}", ""]
        for index, run in enumerate(runs, start=1):
            snapshot = run["snapshot"]
            lines.extend(
                [
                    f"Run {index}: {run.get('survey_title') or run.get('survey_slug') or ''}",
                    f"Saved: {run.get('created_at') or 'Unknown time'}",
                    f"Survey: {run.get('survey_slug') or ''}",
                    f"Responses: {run.get('responses_recorded', 0)} / {run.get('total_questions', 0)}",
                    "",
                ]
            )
            for row in _get_export_rows(snapshot):
                lines.extend(
                    textwrap.wrap(
                        f"{row['number']}. {row['question']} [{row['response_kind']}] - {row['response']}",
                        width=92,
                    )
                )
                lines.append("")
        return _minimal_pdf(lines)

    buffer = io.BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(buffer, pagesize=A4)
    font_name = "Helvetica"
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if font_path.exists():
        pdfmetrics.registerFont(TTFont("DejaVuSans", str(font_path)))
        font_name = "DejaVuSans"

    y = page_height - 50

    def draw_line(line: str, *, size: int = 10, gap: int = 14) -> None:
        nonlocal y
        if y < 50:
            pdf.showPage()
            y = page_height - 50
        pdf.setFont(font_name, size)
        pdf.drawString(50, y, line)
        y -= gap

    draw_line("Survey Results Archive", size=16, gap=24)
    draw_line(f"Total saved runs: {len(runs)}", size=10, gap=22)

    for index, run in enumerate(runs, start=1):
        snapshot = run["snapshot"]
        draw_line(f"Run {index}: {run.get('survey_title') or run.get('survey_slug') or ''}", size=13, gap=18)
        draw_line(f"Saved: {run.get('created_at') or 'Unknown time'}")
        draw_line(f"Survey: {run.get('survey_slug') or ''}")
        draw_line(f"Responses: {run.get('responses_recorded', 0)} / {run.get('total_questions', 0)}", gap=18)

        for row in _get_export_rows(snapshot):
            wrapped_lines = textwrap.wrap(
                f"{row['number']}. {row['question']} [{row['response_kind']}] - {row['response']}",
                width=105,
            ) or [""]
            for line in wrapped_lines:
                draw_line(line)
            y -= 4
        y -= 8

    pdf.save()
    buffer.seek(0)
    return buffer.read()


def _snapshot_to_xlsx(snapshot: dict) -> bytes:
    rows = [[
        "Number",
        "Question ID",
        "Question",
        "Type",
        "Response",
        "Raw Input",
    ]]
    for row in _get_export_rows(snapshot):
        rows.append([
            row["number"],
            row["question_id"],
            row["question"],
            row["response_kind"],
            row["response"],
            row["raw_input"],
        ])

    summary = [
        ["Field", "Value"],
        ["Survey", snapshot.get("survey_id", "")],
        ["Phase", snapshot.get("phase", "")],
        ["Responses Recorded", snapshot.get("responses_recorded", 0)],
        ["Total Questions", snapshot.get("total_questions", len(rows) - 1)],
        ["Contact Info", snapshot.get("contact_info", "")],
        ["Started At", snapshot.get("started_at", "")],
    ]
    return build_xlsx([
        ("Summary", summary),
        ("Responses", rows),
    ])


def _runs_to_xlsx(runs: list[dict]) -> bytes:
    summary_rows = [[
        "Run ID",
        "Survey Slug",
        "Survey Title",
        "Started At",
        "Saved At",
        "Responses Recorded",
        "Total Questions",
        "Contact Info",
        "Phase",
    ]]
    response_rows = [[
        "Run ID",
        "Survey Slug",
        "Survey Title",
        "Number",
        "Question ID",
        "Question",
        "Type",
        "Response",
        "Raw Input",
    ]]

    for index, run in enumerate(runs, start=1):
        snapshot = run["snapshot"]
        run_id = run.get("id") or index
        summary_rows.append([
            run_id,
            run.get("survey_slug"),
            run.get("survey_title"),
            run.get("started_at"),
            run.get("created_at"),
            run.get("responses_recorded", 0),
            run.get("total_questions", 0),
            snapshot.get("contact_info", ""),
            run.get("phase"),
        ])
        for row in _get_export_rows(snapshot):
            response_rows.append([
                run_id,
                run.get("survey_slug"),
                run.get("survey_title"),
                row["number"],
                row["question_id"],
                row["question"],
                row["response_kind"],
                row["response"],
                row["raw_input"],
            ])

    return build_xlsx([
        ("Survey Runs", summary_rows),
        ("Responses", response_rows),
    ])


def _export_survey_catalog_response() -> StreamingResponse:
    surveys = survey_service.list_surveys()
    survey_rows = [[
        "Slug",
        "Title",
        "Description",
        "Question Count",
        "Created At",
        "Updated At",
    ]]
    question_rows = [[
        "Survey Slug",
        "Survey Title",
        "Order",
        "Question Key",
        "Question",
        "Response Type",
        "Options",
        "Allow Skip",
    ]]

    for survey in surveys:
        questions = survey_service.list_questions(survey["slug"])
        survey_rows.append([
            survey["slug"],
            survey["title"],
            survey["description"],
            len(questions),
            survey.get("created_at"),
            survey.get("updated_at"),
        ])
        for question in questions:
            question_rows.append([
                survey["slug"],
                survey["title"],
                question["order_index"],
                question["question_key"],
                question["question"],
                question["response_kind"],
                question["options"],
                "Yes" if question["allow_skip"] else "No",
            ])

    payload = build_xlsx([
        ("Surveys", survey_rows),
        ("Survey Questions", question_rows),
    ])
    filename = f"survey_catalog_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.xlsx"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_snapshot_response(snapshot: dict, *, format: Literal["word", "pdf", "xlsx"]) -> StreamingResponse:
    snapshot_survey_id = str(snapshot.get("survey_id") or "")
    filename_base = _safe_filename(snapshot_survey_id, "survey_results")
    started_at_str = snapshot.get("started_at")
    ts_suffix = ""
    if started_at_str:
        try:
            dt = datetime.fromisoformat(started_at_str)
            ts_suffix = f"_{dt.strftime('%Y%m%dT%H%M%SZ')}"
        except ValueError:
            pass
    if format == "word":
        payload = _snapshot_to_word_doc(snapshot).encode("utf-8")
        media_type = "application/msword; charset=utf-8"
        filename = f"{filename_base}_results{ts_suffix}.doc"
    elif format == "xlsx":
        payload = _snapshot_to_xlsx(snapshot)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{filename_base}_results{ts_suffix}.xlsx"
    else:
        payload = _snapshot_to_pdf(snapshot)
        media_type = "application/pdf"
        filename = f"{filename_base}_results{ts_suffix}.pdf"

    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_archive_response(runs: list[dict], *, format: Literal["word", "pdf", "xlsx"]) -> StreamingResponse:
    if not runs:
        raise LookupError("No saved survey runs available.")

    now_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if format == "word":
        payload = _runs_to_word_doc(runs).encode("utf-8")
        media_type = "application/msword; charset=utf-8"
        filename = f"survey_results_archive_{now_ts}.doc"
    elif format == "xlsx":
        payload = _runs_to_xlsx(runs)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"survey_results_archive_{now_ts}.xlsx"
    else:
        payload = _runs_to_pdf(runs)
        media_type = "application/pdf"
        filename = f"survey_results_archive_{now_ts}.pdf"

    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/options")
async def get_survey_options():
    try:
        return {
            "options": survey_service.get_available_options(),
            "active": survey_service.get_active_selection(),
        }
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/active")
async def get_active_survey():
    try:
        return survey_service.get_active_selection()
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/export/status")
async def get_survey_export_status():
    try:
        if _resolve_latest_snapshot_path() is None:
            return _build_export_status()

        snapshot = _load_latest_survey_snapshot()
        status = _build_export_status(snapshot)
        if status["available"]:
            status["archive"] = survey_service.archive_snapshot(
                snapshot,
                source_updated_at=_latest_survey_snapshot_updated_at(),
            )
        return status
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/export/latest")
async def export_latest_survey_results(
    format: Literal["word", "pdf", "xlsx"] = "pdf",
    survey_slug: str | None = Query(default=None),
):
    try:
        snapshot = _load_latest_survey_snapshot()
        status = _build_export_status(snapshot)
        if not status["available"]:
            raise ValueError("Survey export is available after the survey is concluded.")

        snapshot_survey_id = str(snapshot.get("survey_id") or "")
        if survey_slug and snapshot_survey_id != survey_slug:
            raise ValueError("Latest concluded survey does not match the selected survey.")

        survey_service.archive_snapshot(
            snapshot,
            source_updated_at=_latest_survey_snapshot_updated_at(),
        )
        return _export_snapshot_response(snapshot, format=format)
    except Exception as e:
        _raise_survey_http_exception(e)


@router.post("/archive/latest")
async def archive_latest_survey_results():
    try:
        snapshot = _load_latest_survey_snapshot()
        return {
            "item": survey_service.archive_snapshot(
                snapshot,
                source_updated_at=_latest_survey_snapshot_updated_at(),
            )
        }
    except Exception as e:
        _raise_survey_http_exception(e)


@router.delete("/cache/latest")
async def delete_latest_survey_cache():
    try:
        deleted = False
        if SURVEY_SNAPSHOTS_DIR.is_dir():
            for f in SURVEY_SNAPSHOTS_DIR.iterdir():
                if f.suffix == ".json" and f.is_file():
                    f.unlink()
                    deleted = True
        if SURVEY_EXPORT_PATH_LEGACY.exists():
            SURVEY_EXPORT_PATH_LEGACY.unlink()
            deleted = True
        return {"success": True, "deleted": deleted}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/runs")
async def list_survey_runs(survey_slug: str | None = Query(default=None)):
    try:
        return {"items": survey_service.list_runs(survey_slug=survey_slug)}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.delete("/runs")
async def delete_survey_runs(survey_slug: str | None = Query(default=None)):
    try:
        deleted = survey_service.delete_runs(survey_slug=survey_slug)
        return {"success": True, "deleted": deleted}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/runs/export")
async def export_survey_runs(
    format: Literal["word", "pdf", "xlsx"] = "pdf",
    survey_slug: str | None = Query(default=None),
):
    try:
        runs = survey_service.get_run_snapshots(survey_slug=survey_slug)
        return _export_archive_response(runs, format=format)
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/runs/{run_id}")
async def get_survey_run(run_id: int):
    try:
        return {"item": survey_service.get_run_detail(run_id)}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/runs/{run_id}/export")
async def export_survey_run(run_id: int, format: Literal["word", "pdf", "xlsx"] = "pdf"):
    try:
        snapshot = survey_service.get_run_snapshot(run_id)
        return _export_snapshot_response(snapshot, format=format)
    except Exception as e:
        _raise_survey_http_exception(e)


@router.delete("/runs/{run_id}")
async def delete_survey_run(run_id: int):
    try:
        survey_service.delete_run(run_id)
        return {"success": True}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.post("/update")
async def update_survey_config(survey_slug: str):
    try:
        active = survey_service.set_active_selection(survey_slug=survey_slug)
        return {
            "success": True,
            "active": active,
            "survey_preview": survey_service.get_runtime_survey(active["survey_slug"]),
        }
    except Exception as e:
        _raise_survey_http_exception(e)

@router.get("")
async def list_surveys():
    try:
        return {"items": survey_service.list_surveys()}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.get("/export/catalog")
async def export_survey_catalog():
    try:
        return _export_survey_catalog_response()
    except Exception as e:
        _raise_survey_http_exception(e)


@router.post("")
async def create_survey(payload: SurveyCreateRequest):
    try:
        return {"item": survey_service.create_survey(**payload.model_dump())}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.put("/{survey_id}")
async def update_survey(survey_id: str, payload: SurveyUpdateRequest):
    try:
        return {"item": survey_service.update_survey(survey_id, **payload.model_dump())}
    except Exception as e:
        _raise_survey_http_exception(e)

@router.delete("/{survey_id}")
async def delete_survey(survey_id: str):
    try:
        survey_service.delete_survey(survey_id)
        return {"success": True}
    except Exception as e:
        _raise_survey_http_exception(e)

@router.get("/{survey_id}/questions")
async def list_questions(survey_id: str):
    try:
        return {"items": survey_service.list_questions(survey_id)}
    except Exception as e:
        _raise_survey_http_exception(e)


@router.post("/{survey_id}/questions")
async def create_question(survey_id: str, payload: SurveyQuestionCreateRequest):
    try:
        return {
            "item": survey_service.create_question(
                survey_id=survey_id,
                **payload.model_dump(),
            )
        }
    except Exception as e:
        _raise_survey_http_exception(e)
    
@router.put("/questions/{question_id}")
async def update_question(question_id: str, payload: SurveyQuestionUpdateRequest):
    try:
        return {"item": survey_service.update_question(question_id, **payload.model_dump())}
    except Exception as e:
        _raise_survey_http_exception(e)
    

@router.delete("/questions/{question_id}")
async def delete_question(question_id: str):
    try:
        survey_service.delete_question(question_id)
        return {"success": True}
    except Exception as e:
        _raise_survey_http_exception(e)
    
@router.get("/runtime/{slug}")
async def get_runtime_survey(slug: str):
    try:
        return {"item": survey_service.get_runtime_survey(slug)}
    except Exception as e:
        _raise_survey_http_exception(e)
