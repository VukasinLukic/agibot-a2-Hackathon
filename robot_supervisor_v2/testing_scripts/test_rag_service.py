import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_ROOT = REPO_ROOT / "robot_supervisor_v2"
for path in (REPO_ROOT, SUPERVISOR_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.services.rag_service import RAGService
from rag_service.app import IndexProfile, LocalRAGService, _resolve_device


class RAGServiceEnvironmentTests(unittest.TestCase):
    def test_cpu_is_the_default_device(self) -> None:
        with patch.dict(os.environ, {"RAG_DEVICE": "cuda"}, clear=False):
            env = RAGService("rag-service", {})._build_environment()

        self.assertEqual(env["RAG_DEVICE"], "cpu")
        self.assertEqual(env["RAG_STRICT_DEVICE"], "false")

    def test_explicit_cuda_device_is_forwarded(self) -> None:
        service = RAGService(
            "rag-service",
            {"device": "cuda", "strict_device": True},
        )

        env = service._build_environment()

        self.assertEqual(env["RAG_DEVICE"], "cuda")
        self.assertEqual(env["RAG_STRICT_DEVICE"], "true")

    def test_invalid_device_is_rejected(self) -> None:
        service = RAGService("rag-service", {"device": "gpu"})

        with self.assertRaisesRegex(ValueError, "cpu.*cuda"):
            service._build_environment()


class RAGDeviceResolutionTests(unittest.TestCase):
    @patch("rag_service.app.torch.cuda.is_available", return_value=True)
    def test_cuda_is_selected_when_available(self, _cuda_available) -> None:
        self.assertEqual(_resolve_device("cuda"), "cuda")

    @patch("rag_service.app.torch.cuda.is_available", return_value=False)
    def test_strict_cuda_rejects_cpu_fallback(self, _cuda_available) -> None:
        with patch.dict(os.environ, {"RAG_STRICT_DEVICE": "true"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "CUDA is unavailable"):
                _resolve_device("cuda")

    def test_unknown_device_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected 'cpu' or 'cuda'"):
            _resolve_device("gpu")


class RAGQdrantCompatibilityTests(unittest.TestCase):
    @staticmethod
    def _service_with(client) -> LocalRAGService:
        service = LocalRAGService.__new__(LocalRAGService)
        service._qdrant = client
        service._embed_texts = lambda _texts: [[0.1, 0.2]]
        service.get_query_indexes = lambda: [
            IndexProfile(
                slug="test",
                title="Test",
                collection_name="robot_knowledge_test",
                storage_path="/tmp/test",
            )
        ]
        return service

    def test_new_qdrant_client_uses_query_points(self) -> None:
        point = SimpleNamespace(score=0.9, payload={"text": "Veselin"})

        class Client:
            def query_points(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(points=[point])

        client = Client()
        results = self._service_with(client)._search_points("Veselin", 5)

        self.assertEqual(results[0][0], point)
        self.assertEqual(client.kwargs["query"], [0.1, 0.2])

    def test_old_qdrant_client_falls_back_to_search(self) -> None:
        point = SimpleNamespace(score=0.8, payload={"text": "Veselin"})

        class Client:
            def search(self, **kwargs):
                self.kwargs = kwargs
                return [point]

        client = Client()
        results = self._service_with(client)._search_points("Veselin", 5)

        self.assertEqual(results[0][0], point)
        self.assertEqual(client.kwargs["query_vector"], [0.1, 0.2])


if __name__ == "__main__":
    unittest.main()
