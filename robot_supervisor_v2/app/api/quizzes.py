import io
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from content_service.content_store.quizzes.service import QuizService
from .spreadsheets import build_xlsx

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])
quiz_service = QuizService(include_examples=False)

class QuizCreateRequest(BaseModel):
    slug: str
    title: str
    description: str = ""
    max_attempts: int = 2

class QuizUpdateRequest(QuizCreateRequest):
    pass

class QuestionCreateRequest(BaseModel):
    question_key: str
    question: str
    options: list[str]
    correct_answer: str
    hint: str = ""
    explanation: str = ""
    order_index: int = 0

class QuestionUpdateRequest(QuestionCreateRequest):
    pass

def _raise_quiz_http_exception(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc))
    raise HTTPException(status_code=500, detail=f"Quiz operation failed: {str(exc)}")


def _export_quiz_catalog_response() -> StreamingResponse:
    quizzes = quiz_service.list_quizzes()
    quiz_rows = [[
        "Slug",
        "Title",
        "Description",
        "Max Attempts",
        "Question Count",
        "Created At",
        "Updated At",
    ]]
    question_rows = [[
        "Quiz Slug",
        "Quiz Title",
        "Order",
        "Question Key",
        "Question",
        "Options",
        "Correct Answer",
        "Hint",
        "Explanation",
    ]]

    for quiz in quizzes:
        questions = quiz_service.list_questions(quiz["slug"])
        quiz_rows.append([
            quiz["slug"],
            quiz["title"],
            quiz["description"],
            quiz["max_attempts"],
            len(questions),
            quiz.get("created_at"),
            quiz.get("updated_at"),
        ])
        for question in questions:
            question_rows.append([
                quiz["slug"],
                quiz["title"],
                question["order_index"],
                question["question_key"],
                question["question"],
                question["options"],
                question["correct_answer"],
                question["hint"],
                question["explanation"],
            ])

    payload = build_xlsx([
        ("Quizzes", quiz_rows),
        ("Quiz Questions", question_rows),
    ])
    filename = f"quiz_catalog_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.xlsx"
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/options")
async def get_quiz_options():
    try:
        return {
            "options": quiz_service.get_available_options(),
            "active": quiz_service.get_active_selection(),
        }
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.get("/active")
async def get_active_quiz():
    try:
        return quiz_service.get_active_selection()
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.post("/update")
async def update_quiz_config(quiz_slug: str):
    try:
        active = quiz_service.set_active_selection(quiz_slug=quiz_slug)
        return {
            "success": True,
            "active": active,
            "quiz_preview": quiz_service.get_runtime_quiz(active["quiz_slug"]),
        }
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.get("")
async def list_quizzes():
    try:
        return {"items": quiz_service.list_quizzes()}
    except Exception as e:
        _raise_quiz_http_exception(e)


@router.get("/export")
async def export_quiz_catalog():
    try:
        return _export_quiz_catalog_response()
    except Exception as e:
        _raise_quiz_http_exception(e)


@router.post("")
async def create_quiz(payload: QuizCreateRequest):
    try:
        return {"item": quiz_service.create_quiz(**payload.model_dump())}
    except Exception as e:
        _raise_quiz_http_exception(e)


@router.put("/{quiz_id}")
async def update_quiz(quiz_id: str, payload: QuizUpdateRequest):
    try:
        return {"item": quiz_service.update_quiz(quiz_id, **payload.model_dump())}
    except Exception as e:
        _raise_quiz_http_exception(e)


@router.delete("/{quiz_id}")
async def delete_quiz(quiz_id: str):
    try:
        quiz_service.delete_quiz(quiz_id)
        return {"success": True}
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.get("/{quiz_id}/questions")
async def list_questions(quiz_id: str):
    try:
        return {"items": quiz_service.list_questions(quiz_id)}
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.post("/{quiz_id}/questions")
async def create_question(quiz_id: str, payload: QuestionCreateRequest):
    try:
        return {
            "item": quiz_service.create_question(
                quiz_id=quiz_id,
                **payload.model_dump(),
            )
        }
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.put("/questions/{question_id}")
async def update_question(question_id: str, payload: QuestionUpdateRequest):
    try:
        return {"item": quiz_service.update_question(question_id, **payload.model_dump())}
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.delete("/questions/{question_id}")
async def delete_question(question_id: str):
    try:
        quiz_service.delete_question(question_id)
        return {"success": True}
    except Exception as e:
        _raise_quiz_http_exception(e)

@router.get("/runtime/{slug}")
async def get_runtime_quiz(slug: str):
    try:
        return {"item": quiz_service.get_runtime_quiz(slug)}
    except Exception as e:
        _raise_quiz_http_exception(e)
