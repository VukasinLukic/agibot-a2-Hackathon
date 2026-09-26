"""
Knowledge Service for managing documents and vector database integration.
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, asdict

import httpx
from fastapi import UploadFile
import fitz 

import uuid 
import numpy as np 
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, SearchParams, Filter


logger = logging.getLogger(__name__)


@dataclass
class Document:
    """Document metadata structure."""
    id: str
    filename: str
    chunks: int
    uploaded_at: str
    file_size: int


@dataclass 
class KnowledgeState:
    """Knowledge base state."""
    documents: List[Document]
    total_chunks: int
    last_indexed: Optional[str]


class KnowledgeService:
    """Service for managing knowledge base and document processing."""
    
    def __init__(self, 
                 vector_db_url: str = "http://localhost:6333",
                 collection_name: str = "robot_knowledge",
                 storage_path: str = "./knowledge_storage", 
                 model_name: str = "BAAI/bge-m3",
                 batch_size: int = 10,
                 chunk_size: int = 2500,
                 chunk_overlap: int = 300):
        """Initialize knowledge service."""
        self.vector_db_url = vector_db_url
        self.collection_name = collection_name
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True, parents=True)
        
        # Initalize embedding model 
        self.embedding_model = SentenceTransformer(model_name)
        self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()

        # Initialize Qdrant client
        self.qdrant_client = QdrantClient(url=vector_db_url)
        
        # Ensure collection exists
        logger.info(f"Knowledge service initialized with model: {model_name}")

        # Batch size 
        self.batch_size = batch_size
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if self.chunk_overlap < 0:
            raise ValueError("chunk_overlap must be >= 0")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
    
    async def _ensure_collection_exists(self): 
        """Ensure Qdrant collection exists."""
        try:
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]
            
            if self.collection_name not in collection_names:
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.embedding_dim,
                        distance=Distance.COSINE
                    )
                )
                logger.info(f"Created collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Failed to ensure collection exists: {e}")


    async def get_knowledge_state(self) -> KnowledgeState:
        """Get current knowledge base state."""
        try:
            # List documents from storage directory
            documents = []
            total_chunks = 0
            last_indexed = None
            
            for doc_file in self.storage_path.glob("*.json"):
                try:
                    import json
                    with open(doc_file, 'r') as f:
                        doc_data = json.load(f)
                    
                    doc = Document(**doc_data)
                    documents.append(doc)
                    total_chunks += doc.chunks
                    
                    # Track most recent document for last_indexed
                    if not last_indexed or doc.uploaded_at > last_indexed:
                        last_indexed = doc.uploaded_at
                        
                except Exception as e:
                    logger.warning(f"Failed to load document metadata {doc_file}: {e}")
                    continue
            
            return KnowledgeState(
                documents=documents,
                total_chunks=total_chunks,
                last_indexed=last_indexed
            )
            
        except Exception as e:
            logger.error(f"Failed to get knowledge state: {e}")
            return KnowledgeState(documents=[], total_chunks=0, last_indexed=None)
    
    async def upload_document(self, file: UploadFile) -> Document:
        """Upload and process a document."""
        try:
            # Validate file type
            if not file.filename.lower().endswith('.pdf'):
                raise ValueError("Only PDF files are supported")
            
            # Generate document ID
            doc_id = f"doc_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
            
            # Save file to storage
            file_path = self.storage_path / f"{doc_id}.pdf"
            content = await file.read()
            
            with open(file_path, 'wb') as f:
                f.write(content)
            
            # Process document (chunking and embedding)
            chunks = await self._process_document(file_path, doc_id)
            
            # Create document metadata
            document = Document(
                id=doc_id,
                filename=file.filename,
                chunks=len(chunks),
                uploaded_at=datetime.now().isoformat(),
                file_size=len(content)
            )
            
            # Save metadata
            metadata_path = self.storage_path / f"{doc_id}.json"
            import json
            with open(metadata_path, 'w') as f:
                json.dump(asdict(document), f, indent=2)
            
            logger.info(f"Document uploaded: {document.filename} with {document.chunks} chunks")
            return document
            
        except Exception as e:
            logger.error(f"Failed to upload document: {e}")
            raise
    
    async def delete_document(self, doc_id: str) -> bool:
        """Delete a document and its data."""
        try:
            # Remove from vector database
            await self._delete_from_vector_db(doc_id)
            
            # Remove files
            pdf_path = self.storage_path / f"{doc_id}.pdf"
            metadata_path = self.storage_path / f"{doc_id}.json"
            
            if pdf_path.exists():
                pdf_path.unlink()
            if metadata_path.exists():
                metadata_path.unlink()
            
            logger.info(f"Document deleted: {doc_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")
            return False
    
    async def index_all_documents(self) -> Dict[str, Any]:
        """Re-index all documents in the vector database."""
        try:
            state = await self.get_knowledge_state()
            indexed_count = 0
            
            for document in state.documents:
                try:
                    pdf_path = self.storage_path / f"{document.id}.pdf"
                    if pdf_path.exists():
                        chunks = await self._process_document(pdf_path, document.id)
                        indexed_count += len(chunks)
                        
                except Exception as e:
                    logger.error(f"Failed to index document {document.id}: {e}")
                    continue
            
            result = {
                "indexed_documents": len(state.documents),
                "total_chunks": indexed_count,
                "timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"Indexed {result['indexed_documents']} documents with {result['total_chunks']} chunks")
            return result
            
        except Exception as e:
            logger.error(f"Failed to index documents: {e}")
            raise
    
    async def _process_document(self, file_path: Path, doc_id: str) -> List[Dict[str, Any]]:
        """Process document: extract text, chunk, and embed."""
        try:
            # Extract text from PDF
            text = await self._extract_pdf_text(file_path)
            
            # Chunk text
            chunks = await self._chunk_text(text, doc_id)
            
            # Store in vector database
            await self._store_in_vector_db(chunks)
            
            return chunks
            
        except Exception as e:
            logger.error(f"Failed to process document {file_path}: {e}")
            raise
    
    async def _extract_pdf_text(self, file_path: Path) -> str:
        """Extract text from PDF file."""
        try:
            
            doc = fitz.open(file_path)
            text = ""
            
            for page in doc:
                text += page.get_text()
            
            doc.close()
            return text
            
        except Exception as e:
            logger.error(f"Failed to extract text from {file_path}: {e}")
            raise
    
    async def _chunk_text(self, text: str, doc_id: str) -> List[Dict[str, Any]]:
        """Chunk text into smaller segments."""
        # Simple chunking by character count
        chunk_size = self.chunk_size
        overlap = self.chunk_overlap
        
        chunks = []
        start = 0
        chunk_id = 0
        
        while start < len(text):
            end = start + chunk_size
            chunk_text = text[start:end]
            
            # Try to end at sentence boundary
            if end < len(text):
                last_period = chunk_text.rfind('.')
                if last_period > chunk_size // 2:
                    end = start + last_period + 1
                    chunk_text = text[start:end]
            
            chunks.append({
                "id": f"{doc_id}_chunk_{chunk_id}",
                "document_id": doc_id,
                "text": chunk_text.strip(),
                "start_pos": start,
                "end_pos": end
            })
            
            start = end - overlap
            chunk_id += 1
        
        return chunks
    
    async def search_knowledge(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search knowledge base for relevant chunks."""
        try:
            # Generate query embedding
            query_embedding = self._generate_embeddings([query])[0]
            
            # Search in Qdrant
            search_results = self.qdrant_client.query_points(
                collection_name=self.collection_name,
                query=query_embedding.tolist(),
                limit=limit,
                search_params=SearchParams(hnsw_ef=128)
            )
            
            # Format results
            results = []
            for hit in search_results.points:
                results.append({
                    "score": hit.score,
                    "document_id": hit.payload["document_id"],
                    "chunk_id": hit.payload["chunk_id"], 
                    "text": hit.payload["text"],
                    "start_pos": hit.payload["start_pos"],
                    "end_pos": hit.payload["end_pos"]
                })
            
            logger.info(f"Found {len(results)} relevant chunks for query")
            return results
            
        except Exception as e:
            logger.error(f"Failed to search knowledge base: {e}")
            return []
        
    async def _delete_from_vector_db(self, doc_id: str) -> None:
        """Delete document chunks from vector database."""
        try:
            # Delete all points with matching document_id
            self.qdrant_client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(
                    must=[
                        {"key": "document_id", "match": {"value": doc_id}}
                    ]
                )
            )
            
            logger.info(f"Deleted document {doc_id} from vector database")
            
        except Exception as e:
            logger.error(f"Failed to delete from vector DB: {e}")
            raise
    
    async def _store_in_vector_db(self, chunks: List[Dict[str, Any]]) -> None:
        """Store chunks in vector database."""
        try:
            if not chunks:
                return
                
            # Generate embeddings
            texts = [chunk["text"] for chunk in chunks]
            embeddings = self._generate_embeddings(texts)
            
            # Create points for Qdrant
            points = []
            for chunk, embedding in zip(chunks, embeddings):
                point_id = self._stable_uuid(chunk["id"])
                
                points.append(PointStruct(
                    id=point_id,
                    vector=embedding.tolist(),
                    payload={
                        "document_id": chunk["document_id"],
                        "chunk_id": chunk["id"],
                        "text": chunk["text"],
                        "start_pos": chunk["start_pos"],
                        "end_pos": chunk["end_pos"]
                    }
                ))

            # Upsert in batches
            for i in range(0, len(points), self.batch_size):
                batch = points[i:i + self.batch_size]
                self.qdrant_client.upsert(
                    collection_name=self.collection_name,
                    points=batch
                )
            
            logger.info(f"Stored {len(chunks)} chunks in vector database")
            
        except Exception as e:
            logger.error(f"Failed to store chunks in vector DB: {e}")
            raise

    async def close(self):
        """Clean up resources."""
        if self.client:
            await self.client.aclose()

    def _generate_embeddings(self, texts: List[str]) -> np.ndarray:
        """Generate embeddings for text chunks."""
        return self.embedding_model.encode(texts, normalize_embeddings=True)

    def _stable_uuid(self, text: str) -> str:
        """Generate stable UUID for text."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, text))

# Global service instance
knowledge_service: Optional[KnowledgeService] = None


async def get_knowledge_service() -> KnowledgeService:
    """Get the global knowledge service instance."""
    global knowledge_service
    
    if knowledge_service is None:
        # Initialize with configuration
        storage_path = os.getenv("KNOWLEDGE_STORAGE_PATH", "./knowledge_storage")
        vector_db_url = os.getenv("VECTOR_DB_URL", "http://localhost:6333")
        collection_name = os.getenv("KNOWLEDGE_COLLECTION_NAME", "robot_knowledge")
        embedding_model = os.getenv("KNOWLEDGE_EMBEDDING_MODEL", "BAAI/bge-m3")
        batch_size = int(os.getenv("KNOWLEDGE_BATCH_SIZE", "10"))
        chunk_size = int(os.getenv("KNOWLEDGE_CHUNK_SIZE", "2500"))
        chunk_overlap = int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP", "300"))
        
        knowledge_service = KnowledgeService(
            vector_db_url=vector_db_url,
            collection_name=collection_name,
            storage_path=storage_path,
            model_name=embedding_model,
            batch_size=batch_size,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    
    return knowledge_service
