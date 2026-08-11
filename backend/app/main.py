import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .api.routes import router
from .database import init_db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    try:
        from .system_log import install_handler, purge_old
        install_handler()
        purge_old()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("系统日志初始化失败")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Stock AI - A 股 AI 模拟交易", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/api/health", include_in_schema=False)
def health():
    return {"ok": True}


# 托管前端: Docker 用 backend/static；本地开发优先 frontend/dist
_backend_root = Path(__file__).resolve().parent.parent
_candidates = [
    _backend_root / "static",
    _backend_root.parent / "frontend" / "dist",
]
STATIC_DIR = next((p for p in _candidates if (p / "index.html").is_file()), None)

if STATIC_DIR is not None:
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # 勿吞掉 API（router 已注册；此处仅兜底非 API 路径）
        if full_path.startswith("api"):
            from fastapi import HTTPException
            raise HTTPException(404, "Not Found")
        file = STATIC_DIR / full_path
        if full_path and file.is_file():
            return FileResponse(file)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def root_hint():
        return {
            "message": "API 已启动，前端未构建。请访问开发前端或先 npm run build",
            "api_status": "/api/status",
            "docs": "/docs",
            "dev_ui": "http://127.0.0.1:5174",
        }
