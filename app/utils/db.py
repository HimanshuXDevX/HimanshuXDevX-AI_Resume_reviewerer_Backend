import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from dotenv import load_dotenv
from app.models.user import User

load_dotenv()

logger = logging.getLogger("uvicorn.error")

_db_initialized = False
_client = None


async def init_db():
    global _db_initialized, _client

    if _db_initialized:
        return 

    MONGO_URI = os.getenv("MONGO_URI")
    DB_NAME = os.getenv("DB_NAME")

    if not MONGO_URI or not DB_NAME:
        raise ValueError(" Missing MONGO_URI or DB_NAME in environment variables ")

    logger.info(" Connecting to MongoDB...")
    _client = AsyncIOMotorClient(MONGO_URI)
    db = _client[DB_NAME]

    logger.info(" Initializing Beanie with models...")
    await init_beanie(database=db, document_models=[User])

    _db_initialized = True
    logger.info(" Database initialized successfully.")
