from pydantic import BaseModel


class KnowledgeUploadResponse(BaseModel):
    filename: str
    thread_id: str
    chunks: int
    message: str
