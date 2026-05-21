import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.documents import Document
from pymilvus import connections, utility

logger = logging.getLogger(__name__)

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except Exception as exc:
    logger.warning("langchain_text_splitters unavailable, using local splitter: %s", exc)

    class RecursiveCharacterTextSplitter:
        def __init__(
            self,
            chunk_size: int = 500,
            chunk_overlap: int = 50,
            length_function=len,
            separators: Optional[list[str]] = None,
        ):
            self.chunk_size = chunk_size
            self.chunk_overlap = max(0, min(chunk_overlap, chunk_size - 1))
            self.length_function = length_function
            self.separators = separators or ["\n\n", "\n", " ", ""]

        def create_documents(self, texts: list[str], metadatas: Optional[list[dict]] = None) -> list[Document]:
            documents: list[Document] = []
            metadatas = metadatas or [{} for _ in texts]
            for text, metadata in zip(texts, metadatas):
                for chunk in self._split_text(text):
                    documents.append(Document(page_content=chunk, metadata=dict(metadata)))
            return documents

        def _split_text(self, text: str) -> list[str]:
            chunks: list[str] = []
            start = 0
            text_length = len(text)
            while start < text_length:
                end = min(start + self.chunk_size, text_length)
                chunks.append(text[start:end])
                if end >= text_length:
                    break
                start = max(0, end - self.chunk_overlap)
            return [chunk for chunk in chunks if chunk.strip()]


# 使用 langchain-milvus 新包（类名是 Milvus，不是 MilvusVectorStore）
try:
    from langchain_milvus import Milvus as _MilvusVectorStore
    _MILVUS_BACKEND = "langchain_milvus"
except ImportError:
    from langchain_community.vectorstores import Milvus as _MilvusVectorStore
    _MILVUS_BACKEND = "langchain_community"


@dataclass(frozen=True)
class RAGConfig:
    milvus_host: str = "127.0.0.1"
    milvus_port: int = 19530
    collection_name: str = "mult_agent_knowledge"
    embedding_model: str = "text-embedding-v1"
    chunk_size: int = 500
    chunk_overlap: int = 50


class RAGSystem:
    def __init__(self, api_key: str, config: Optional[RAGConfig] = None):
        self.config = config or RAGConfig()
        self.api_key = api_key
        self.embeddings = DashScopeEmbeddings(
            model=self.config.embedding_model,
            dashscope_api_key=self.api_key,
        )
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        )
        self._connect_to_milvus()
        self.vectorstore = _MilvusVectorStore(
            embedding_function=self.embeddings,
            collection_name=self.config.collection_name,
            connection_args={"uri": f"http://{self.config.milvus_host}:{self.config.milvus_port}"},
            auto_id=True,
        )
        logger.info("RAG backend=%s | collection=%s", _MILVUS_BACKEND, self.config.collection_name)

    def _connect_to_milvus(self) -> None:
        try:
            connections.connect(
                alias="default",
                host=self.config.milvus_host,
                port=self.config.milvus_port,
            )
        except Exception as exc:
            logger.error("连接 Milvus 失败: %s", exc)

    def search(self, query: str, k: int = 3) -> str:
        try:
            records = self.search_records(query, k=k)
            if not records:
                return "未找到相关信息。"
            lines: list[str] = ["检索到的相关信息："]
            for idx, record in enumerate(records, 1):
                lines.append(f"{idx}. {record['snippet']}")
                lines.append(f"   (来源: {record['doc_id']})")
            return "\n".join(lines)
        except Exception as exc:
            logger.error("检索失败: %s", exc)
            return f"检索过程中发生错误: {str(exc)}"

    def search_records(self, query: str, k: int = 5, metadata_filter: Optional[dict] = None) -> list[dict]:
        if not utility.has_collection(self.config.collection_name):
            return []
        search_k = max(k * 5, 20) if metadata_filter else k
        docs = self.vectorstore.similarity_search(query, k=search_k)
        records: list[dict] = []
        for doc in docs:
            metadata = doc.metadata or {}
            if metadata_filter:
                matched = True
                for key, expected in metadata_filter.items():
                    if expected is None or str(expected).strip() == "":
                        continue
                    if str(metadata.get(key, "")).strip() != str(expected).strip():
                        matched = False
                        break
                if not matched:
                    continue
            source = str(metadata.get("source") or "").strip()
            title = str(metadata.get("title") or "").strip() or (Path(source).name if source else f"本地知识片段-{len(records) + 1}")
            records.append(
                {
                    "source_id": f"LOC-{len(records) + 1}",
                    "doc_id": str(metadata.get("doc_id") or source),
                    "title": title,
                    "snippet": doc.page_content,
                    "source_type": "local",
                    "metadata": metadata,
                }
            )
            if len(records) >= k:
                break
        return records

    def add_documents(self, documents: list[Document]) -> int:
        self.vectorstore.add_documents(documents)
        return len(documents)

    def ingest_text(self, text: str, source: str, metadata: Optional[dict] = None) -> int:
        doc_metadata = dict(metadata or {})
        doc_metadata.setdefault("source", source)
        doc_metadata.setdefault("title", Path(source).name if source else "uploaded_document")
        title = str(doc_metadata.get("title") or source or "uploaded_document")
        docs = self.text_splitter.create_documents([text], metadatas=[doc_metadata])
        for index, doc in enumerate(docs, 1):
            doc.metadata["chunk_index"] = index
            doc.page_content = f"文件名：{title}\n来源：{source}\n片段：{doc.page_content}"
        return self.add_documents(docs)

    def ingest_paths(self, paths: Iterable[Path]) -> int:
        total = 0
        for path in paths:
            text = path.read_text(encoding="utf-8")
            total += self.ingest_text(text, source=str(path))
        return total
