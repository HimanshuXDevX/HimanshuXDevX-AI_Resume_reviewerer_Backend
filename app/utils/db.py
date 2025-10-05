import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from app.models.user import User
from dotenv import load_dotenv
import asyncio

load_dotenv()

logger = logging.getLogger("uvicorn.error")

_client = None
_db_initialized = False


async def init_db():
    global _client, _db_initialized

    if _db_initialized:
        # Double-check if event loop is still running
        try:
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                _db_initialized = False
        except RuntimeError:
            _db_initialized = False

    if not _db_initialized:
        mongo_uri = os.getenv("MONGO_URI")
        db_name = os.getenv("DB_NAME")

        if not mongo_uri or not db_name:
            raise ValueError("Missing MONGO_URI or DB_NAME in environment variables")

        logger.info("Connecting to MongoDB...")

        # Create new client for this loop
        _client = AsyncIOMotorClient(mongo_uri)
        db = _client[db_name]

        logger.info("Initializing Beanie with models...")
        await init_beanie(database=db, document_models=[User])

        _db_initialized = True
        logger.info("Database initialized successfully.")
