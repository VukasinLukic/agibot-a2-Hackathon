from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from utils.logging_utils import global_logger
from typing import Optional, List, Dict, Any
from utils.openai_utils import get_openai_embedding
from azure.search.documents.models import (
    VectorizedQuery,
)

from utils.env_vars import AI_SEARCH_ENDPOINT, AI_SEARCH_ADMIN_KEY, INDEX_NAME, VECTOR_FIELD, LLM_CONTEXT_RESULTS

k_vec_map = {1: 33, 2: 22, 3: 16, 4: 13, 5: 11}

content_field = 'chunk_content'
metadata_fields = ['container', 'filename']
select_fields = [content_field] + metadata_fields

class AISearchIndex:
    def __init__(self, index_name: str):
        self.index_name = index_name
        try:
            self.search_client: SearchClient = SearchClient(
                endpoint=AI_SEARCH_ENDPOINT,
                index_name=index_name,
                credential=AzureKeyCredential(AI_SEARCH_ADMIN_KEY),
            )
        except Exception as e:
            global_logger.error(f"Failed to initialize SearchClient for index {self.index_name}: {e}")
            raise e
        global_logger.info(f"SearchClient succesfully initialized for index {self.index_name}")

    def search(self,
               search_text: str,
               vector_queries: List[VectorizedQuery],
               semantic_configuration_name: str,
               select: Optional[List[str]] = None,
               filter_expression: Optional[str] = None,
               top: int = 5,
               **kwargs: Any
               ) -> List[Dict[str, Any]]:

        search_params = {
            "search_text": search_text,
            "vector_queries": vector_queries,
            "select": select,
            "filter": filter_expression,
            "top": top,
            "include_total_count": False,
            "vector_filter_mode": "preFilter",
            "query_type": "semantic",
            "semantic_configuration_name": semantic_configuration_name,
            "query_caption": None,
            "query_answer": None,
        }

        search_params.update(kwargs)
        search_params = {k: v for k, v in search_params.items() if v is not None}

        try:
            results_iterator = self.search_client.search(**search_params)
            results = [dict(doc) for doc in results_iterator]
            global_logger.info(f"Search completed successfully for index {self.index_name}")
            return results
        except Exception as e:
            global_logger.error(f"Search failed for index {self.index_name}: {e}")
            raise e
        
    def search_wrapper(self, query: str) -> str:
        top = LLM_CONTEXT_RESULTS

        to_vectorize = [query] #+ keywords
        num_vecs = len(to_vectorize)
        knn = k_vec_map.get(num_vecs, 10)

        vectors = []

        global_logger.info(f"TO vec: {to_vectorize}")

        for text in to_vectorize:
            vec, llm_fail = get_openai_embedding(text)
            if llm_fail:
                return "Failed to generate embedding due to OpenAI Content Filters"
            vectors.append(vec)

        vector_queries = [
            VectorizedQuery(
                vector=vector,
                fields=VECTOR_FIELD,
                k_nearest_neighbors=knn,
            ) for vector in vectors]
        
        results = self.search(query, vector_queries, select=select_fields, top=top, semantic_configuration_name="semantic-config")

        return format_search_results_as_context(results)


        
kb_vector_index = AISearchIndex(INDEX_NAME)

def format_search_results_as_context(results_kb):
    context_parts = []
    for chunk in results_kb:
        parts = [f"{'-'*5}"]
        
        # Build metadata line with only non-empty fields
        metadata_parts = []
        for field in metadata_fields:
            value = chunk.get(field)
            if value:
                metadata_parts.append(value)
        
        if metadata_parts:
            parts.append(" | ".join(metadata_parts))
        
        content = chunk.get(content_field)
        if content:
            parts.append(content)
        
        context_parts.append("\n".join(parts))
    
    return "\n".join(context_parts)