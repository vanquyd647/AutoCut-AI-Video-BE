from .routes_analyze import router as analyze_router
from .routes_edit import router as edit_router
from .routes_export import router as export_router
from .routes_project import router as project_router
from .routes_upload import router as upload_router

ROUTERS = [upload_router, analyze_router, edit_router, export_router, project_router]

__all__ = ["ROUTERS"]