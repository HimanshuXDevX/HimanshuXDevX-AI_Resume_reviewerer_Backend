import os
import logging
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from app.models.user import User
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("uvicorn.error")

_db_initialized = False
_db_lock = asyncio.Lock()
_client = None

async def init_db():
    global _db_initialized, _client
    async with _db_lock:
        if _db_initialized:
            return

        mongo_uri = os.getenv("MONGO_URI")
        db_name = os.getenv("DB_NAME")
        if not mongo_uri or not db_name:
            raise ValueError("Missing MONGO_URI or DB_NAME in environment variables")

        logger.info("Connecting to MongoDB...")
        _client = AsyncIOMotorClient(mongo_uri)
        db = _client[db_name]

        logger.info("Initializing Beanie with models...")
        await init_beanie(database=db, document_models=[User])

        _db_initialized = True
        logger.info("Database initialized successfully.")
