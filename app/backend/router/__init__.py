from .auth_router import router as auth_router
from .health_router import router as health_router
from .kb_router import router as kb_router
from .research_router import router as research_router

__all__ = ["auth_router", "health_router", "kb_router", "research_router"]
