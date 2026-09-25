from types import SimpleNamespace

from qdrant_client import models

from rag_service.hall_of_fame import (
    COLLECTION_NAME,
    HallOfFameStore,
    retrieved_panel_context,
    embedding_text,
    normalize_retrieval_question,
    panel_ordinal_aliases,
    panel_sequence_from_question,
    with_panel_ordinal_aliases,
)


class FakeEncoder:
    def __init__(self):
        self.last_text = None

    def encode_one(self, _text):
        self.last_text = _text
        return [0.1] * 1024, models.SparseVector(indices=[10], values=[0.8])


class FakeReranker:
    def scores(self, _question, records):
        return [1.0 if record["id"] == "panel-best" else 0.0 for record in records]


class FakeClient:
    def __init__(self):
        self.calls = []

    def query_points(self, collection_name, **kwargs):
        self.calls.append((collection_name, kwargs))
        if kwargs.get("prefetch"):
            points = [
                SimpleNamespace(payload={"id": "panel-first", "type": "panel"}, score=0.9),
                SimpleNamespace(payload={"id": "panel-best", "type": "panel"}, score=0.8),
            ]
        else:
            points = [SimpleNamespace(payload={"id": "panel-01", "spoken": "Hello"}, score=None)]
        return SimpleNamespace(points=points)


def test_embedding_text_uses_only_required_fields():
    record = {
        "context_prefix": "prefix",
        "spoken": "spoken",
        "detail": "detail",
        "source_text": "must not be embedded",
        "title": "must not be embedded either",
    }
    assert embedding_text(record) == "prefix\nspoken\ndetail"


def test_active_panel_context_has_one_panel_and_full_detail():
    context = retrieved_panel_context(
        {
            "title": "Beginnings",
            "sequence": 1,
            "spoken": "Our story starts with an athlete.",
            "detail": "Veselin earned a scholarship.",
            "source_text": "He was eighteen years old.",
        }
    )
    assert context.count("[RETRIEVED PANEL FOR THIS TURN]") == 1
    assert "title: Beginnings" in context
    assert "sequence: 1" in context
    assert "narration: Our story starts with an athlete." in context
    assert "detail: Veselin earned a scholarship. He was eighteen years old." in context
    assert "story:" not in context
    assert "neighbors:" not in context


def test_cern_spellings_are_canonicalized_only_for_retrieval():
    for question in (
        "Tell me about C E R N",
        "Tell me about C.E.R.N.",
        "What is see e are en openlab?",
        "Did Comtrade work with sirn?",
        "Tell me about sern",
    ):
        normalized = normalize_retrieval_question(question)
        assert "CERN openlab Large Hadron Collider" in normalized
    assert normalize_retrieval_question("Tell me about Citrix") == "Tell me about Citrix"


def test_unqualified_founder_question_is_grounded_as_comtrade_intent():
    normalized = normalize_retrieval_question("Who founded the company?")
    assert normalized.startswith("Who founded the company?")
    assert "Comtrade founder Veselin Jevrosimovic beginnings" in normalized


def test_every_panel_gets_numeric_cardinal_and_ordinal_aliases():
    assert panel_ordinal_aliases(1) == [
        "panel 1", "1. panel", "1st panel", "panel one", "first panel", "the first panel"
    ]
    assert "twenty first panel" in panel_ordinal_aliases(21)
    assert "panel thirty seven" in panel_ordinal_aliases(37)
    assert "last panel" not in panel_ordinal_aliases(37)
    assert "panel forty" in panel_ordinal_aliases(40)
    assert "last panel" in panel_ordinal_aliases(40)
    enriched = with_panel_ordinal_aliases(
        {"id": "panel-02", "type": "panel", "sequence": 2, "aliases": ["First Sparks"]}
    )
    assert enriched["aliases"] == [
        "First Sparks", "panel 2", "2. panel", "2nd panel", "panel two", "second panel", "the second panel"
    ]


def test_explicit_panel_number_parsing_is_deterministic():
    cases = {
        "Tell me about the first panel": 1,
        "Tell me about panel one": 1,
        "What is on 1. panel?": 1,
        "Open panel 2": 2,
        "Tell me about the twenty-first panel": 21,
        "Panel thirty seven please": 37,
        "Tell me about panel forty": 40,
        "Tell me about the final panel": 40,
        "Tell me about the first business connections": None,
        "Panel 99": None,
    }
    for query, expected in cases.items():
        assert panel_sequence_from_question(query) == expected


def test_activate_panel_is_deterministic_payload_filter():
    client = FakeClient()
    result = HallOfFameStore(client=client, encoder=FakeEncoder()).activate_panel("panel-01")
    assert result == {"id": "panel-01", "spoken": "Hello"}
    collection, kwargs = client.calls[0]
    assert collection == COLLECTION_NAME
    condition = kwargs["query_filter"].must[0]
    assert condition.key == "id"
    assert condition.match.value == "panel-01"
    assert kwargs["limit"] == 1


def test_voice_search_hybrid_filters_panels_and_returns_one_reranked_hit():
    client = FakeClient()
    encoder = FakeEncoder()
    store = HallOfFameStore(
        client=client,
        encoder=encoder,
        reranker=FakeReranker(),
    )
    point = store.voice_search("Tell me about C E R N", k=3)
    assert point.payload["id"] == "panel-best"
    assert "CERN openlab Large Hadron Collider" in encoder.last_text

    collection, kwargs = client.calls[0]
    assert collection == COLLECTION_NAME
    assert kwargs["query"].fusion == models.Fusion.RRF
    assert len(kwargs["prefetch"]) == 2
    assert {prefetch.using for prefetch in kwargs["prefetch"]} == {"dense", "sparse"}
    for prefetch in kwargs["prefetch"]:
        condition = prefetch.filter.must[0]
        assert condition.key == "type"
        assert condition.match.value == "panel"


def test_voice_search_bypasses_models_for_explicit_panel_number():
    client = FakeClient()
    encoder = FakeEncoder()
    point = HallOfFameStore(client=client, encoder=encoder).voice_search(
        "Tell me about the second panel"
    )
    assert point.payload["id"] == "panel-01"
    assert encoder.last_text is None
    _, kwargs = client.calls[0]
    conditions = {condition.key: condition.match.value for condition in kwargs["query_filter"].must}
    assert conditions == {"type": "panel", "sequence": 2}
    assert kwargs["limit"] == 1


def test_beginnings_title_is_pinned_to_the_first_physical_panel():
    client = FakeClient()
    encoder = FakeEncoder()
    HallOfFameStore(client=client, encoder=encoder).voice_search("Tell me about the beginnings")
    assert encoder.last_text is None
    conditions = {
        condition.key: condition.match.value
        for condition in client.calls[0][1]["query_filter"].must
    }
    assert conditions == {"type": "panel", "sequence": 1}


def test_known_cross_panel_topics_route_directly_without_model_guessing():
    cases = {
        "When did Comtrade enter the global gaming market?": 23,
        "Tell me about Comtrade System Integration's biggest achievements": 37,
        "Tell me more about the biggest achievements of Comtrade system integration.": 37,
        "Does Comtrade have a Microsoft Partner of the Year award?": 37,
    }
    for query, expected_sequence in cases.items():
        client = FakeClient()
        encoder = FakeEncoder()
        HallOfFameStore(client=client, encoder=encoder).voice_search(query)
        assert encoder.last_text is None
        conditions = {
            condition.key: condition.match.value
            for condition in client.calls[0][1]["query_filter"].must
        }
        assert conditions == {"type": "panel", "sequence": expected_sequence}
