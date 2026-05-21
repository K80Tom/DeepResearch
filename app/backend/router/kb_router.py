import asyncio
from io import BytesIO
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.backend.config import AppSettings
from app.backend.schemas import AuthUser, KnowledgeUploadResponse
from app.backend.service.auth_service import get_current_user
from app.mult_agents.config import AppConfig
from app.mult_agents.rag.core import RAGConfig
from app.mult_agents.tools import ingest_knowledge_text, init_rag_system


router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024


def _extract_pdf_text(content: bytes, filename: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务端未安装 PDF 解析依赖 pypdf，请先安装后再上传 PDF",
        ) from exc
    try:
        reader = PdfReader(BytesIO(content))
        pages = []
        for index, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                pages.append(f"\n\n--- 第 {index} 页 ---\n{page_text}")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{filename} 解析失败：{exc}") from exc
    text = "".join(pages).strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{filename} 没有提取到文本。如果是扫描版 PDF，需要先做 OCR。",
        )
    return text


def _decode_text_file(content: bytes, filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        return _extract_pdf_text(content, filename)
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            text = ""
    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{filename} 不是可读取的文本文件，请先上传 txt/md/csv/json/pdf 等格式",
        )
    return text


def _ensure_rag_ready() -> AppConfig:
    settings = AppSettings()
    config = AppConfig.from_file(settings.config_path)
    rag_config = RAGConfig(
        milvus_host=config.milvus_host,
        milvus_port=config.milvus_port,
        collection_name=config.milvus_collection,
    )
    init_rag_system(api_key=config.api_key, config=rag_config)
    return config


@router.post("/upload", response_model=KnowledgeUploadResponse)
async def upload_knowledge_file(
    file: UploadFile = File(...),
    thread_id: str = Form(..., min_length=1),
    current_user: AuthUser = Depends(get_current_user),
) -> KnowledgeUploadResponse:
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文件不能超过 2MB")
    filename = file.filename or "uploaded_document.txt"
    text = _decode_text_file(raw, filename)
    _ensure_rag_ready()
    metadata = {
        "source": filename,
        "title": filename,
        "doc_id": f"{current_user.tenant_id}/{current_user.id}/{thread_id}/{filename}",
        "tenant_id": current_user.tenant_id,
        "user_id": current_user.id,
        "thread_id": thread_id,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    chunks = await asyncio.to_thread(ingest_knowledge_text, text, filename, metadata)
    return KnowledgeUploadResponse(
        filename=filename,
        thread_id=thread_id,
        chunks=chunks,
        message="文档已写入本地知识库，后续提问可开启本地知识库检索",
    )
