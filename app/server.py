import os
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
import cloudinary
from dotenv import load_dotenv

from app.services.config import settings
from app.utils.db import init_db
from app.routers.resume import router as resume_router

# Load environment variables
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn.error")

# Create FastAPI app
app = FastAPI(
    title="AI Resume Reviewer",
    description="ATS-friendly resume analysis",
    version="0.1.0",
    redoc_url="/redoc",
    docs_url="/docs",
)

# Include routers immediately so they appear in Swagger Docs
app.include_router(resume_router, prefix="/api")

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cloudinary config
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

# Root endpoint
@app.get("/")
@limiter.limit("100/minute")
def read_root(request: Request):
    return {"message": "Welcome to AI Resume Reviewer"}


# Initialize DB at startup (local runs only)
@app.on_event("startup")
async def start_db():
    try:
        logger.info("🚀 Initializing database (startup)...")
        await init_db()
        logger.info("✅ Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {repr(e)}")


# Local run entrypoint
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)