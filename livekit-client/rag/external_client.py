import json
import time
import urllib.error
import urllib.parse
import urllib.request
import logging

logger = logging.getLogger("basic-agent-truebar")


class RAGServiceClient:
    def __init__(
        self,
        *,
        base_url: str,
        search_path: str,
        health_path: str,
        timeout_s: float,
        top_k: int,
        include_scores: bool,
    ) -> None:
        self._search_url = self._join_url(base_url, search_path)
        self._health_url = self._join_url(base_url, health_path)
        self._timeout_s = timeout_s
        self._top_k = top_k
        self._include_scores = include_scores

    @staticmethod
    def _join_url(base_url: str, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return base_url.rstrip("/") + path

    def healthcheck(self) -> None:
        req = urllib.request.Request(self._health_url, method="GET")
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
            if resp.status < 200 or resp.status >= 300:
                raise RuntimeError(f"Healthcheck failed with status={resp.status}")

    def search_wrapper(self, query: str) -> str:
        payload = {
            "query": query,
            "top_k": self._top_k,
            "include_scores": self._include_scores,
        }
        req = urllib.request.Request(
            self._search_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                body = resp.read().decode("utf-8", "replace")
                if resp.status < 200 or resp.status >= 300:
                    raise RuntimeError(f"RAG API returned HTTP {resp.status}: {body[:256]}")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            raise RuntimeError(
                f"RAG API HTTPError status={exc.code} body={body[:256]!r}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"RAG API unreachable at {self._search_url}: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"RAG API request failed: {exc}") from exc
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            logger.debug("External RAG API call elapsed_ms=%.2f", elapsed_ms)

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"RAG API returned invalid JSON: {exc}") from exc

        context = parsed.get("context", "")
        if not isinstance(context, str):
            raise RuntimeError("RAG API response missing string 'context' field")

        return context

