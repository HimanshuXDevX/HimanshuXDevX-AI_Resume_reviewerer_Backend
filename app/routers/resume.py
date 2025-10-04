from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Request
from datetime import datetime
from app.services.generator import ResumeReviewGenerator
from app.models.user import User, Feedback
from app.services.cache import redis_client
from app.utils.auth import authenticate_and_get_user_details
import uuid, json, os, logging
import cloudinary.uploader


logger = logging.getLogger("uvicorn.error")

router = APIRouter(
    prefix="/resume",
    tags=["Resume Analysis"]
)

# POST: analyze resume, upload to Cloudinary, cache in Redis, save/update in MongoDB
@router.post("/analyze", response_model=dict)
async def analyze_resume(
    request: Request,
    jobTitle: str = Form(...),
    jobDescription: str = Form(...),
    resume: UploadFile = File(...),
):
    try:
        logger.info("Start resume analysis")

        # --- Authentication ---
        user_details = authenticate_and_get_user_details(request)
        clerk_id = user_details.get("user_id")
        logger.info(f"Authenticated user with Clerk ID: {clerk_id}")

        # --- Read resume ---
        resume_bytes = await resume.read()
        logger.info(f"Resume file read: {len(resume_bytes)} bytes, content_type={resume.content_type}, filename={resume.filename}")

        # --- Generate feedback ---
        try:
            feedback_data = ResumeReviewGenerator.review_resume(
                file_bytes=resume_bytes,
                content_type=resume.content_type,
                job_title=jobTitle,
                job_description=jobDescription,
            )
            logger.info("Resume feedback generated successfully")
        except Exception as review_error:
            logger.exception("Failed during resume review generation")
            raise HTTPException(status_code=500, detail=f"Resume review error: {review_error}")

        feedback_obj = Feedback(**feedback_data)

        # --- Upload to Cloudinary ---
        try:
            logger.info("Uploading resume to Cloudinary")
            resume_result = cloudinary.uploader.upload(
                resume_bytes,
                resource_type="auto",  # changed to auto to support PDFs/DOCs
                public_id=f"resumes/{uuid.uuid4()}_{resume.filename}",
                type="upload"
            )
            resume_url = resume_result["secure_url"]
            logger.info(f"Uploaded resume to Cloudinary: {resume_url}")
        except Exception as upload_error:
            logger.exception("Cloudinary upload failed")
            raise HTTPException(status_code=500, detail=f"Cloudinary upload failed: {upload_error}")

        image_url = resume_url.replace("/upload/", "/upload/pg_1,f_png/")
        logger.info(f"Generated image URL for preview: {image_url}")

        # --- Cache in Redis ---
        resume_id = str(uuid.uuid4())
        cache_data = {
            "clerk_id": clerk_id,
            "job_title": jobTitle,
            "job_description": jobDescription,
            "resume_url": resume_url,
            "image_url": image_url,
            "feedback": feedback_obj.dict(),
        }
        redis_client.setex(f"resume:{resume_id}", 60 * 60 * 24, json.dumps(cache_data))
        logger.info(f"Cached resume data in Redis under key: resume:{resume_id}")

        # --- Save or update MongoDB ---
        user = await User.find_one({"clerk_id": clerk_id})
        if not user:
            logger.info("Creating new user entry in DB")
            user = User(
                clerk_id=clerk_id,
                resume_url=resume_url,
                image_url=image_url,
                job_title=jobTitle,
                job_description=jobDescription,
                feedback=feedback_obj,
            )
            await user.insert()
            logger.info("New user saved in DB")
        else:
            logger.info("Updating existing user record in DB")
            user.resume_url = resume_url
            user.image_url = image_url
            user.job_title = jobTitle
            user.job_description = jobDescription
            user.feedback = feedback_obj
            user.updated_at = datetime.utcnow()
            await user.save()
            logger.info("User record updated in DB")

        logger.info("Resume analysis completed successfully")

        return {
            "id": resume_id,
            "resume_url": resume_url,
            "image_url": image_url,
            "message": "Resume analyzed, uploaded to Cloudinary, cached, and saved to DB.",
        }

    except Exception as e:
        logger.exception("Unhandled error during resume analysis")
        raise HTTPException(status_code=500, detail=f"Error analyzing resume: {repr(e)}")


# GET: retrieve cached feedback by ID
@router.get("/resume-feedback/{resume_id}", response_model=dict)
async def get_resume_feedback(resume_id: str):
    try:
        logger.info(f"Fetching resume feedback for resume_id: {resume_id}")

        redis_key = f"resume:{resume_id}"
        data = redis_client.get(redis_key)

        if not data:
            logger.warning(f"Resume ID not found or expired in Redis: {redis_key}")
            raise HTTPException(status_code=404, detail="Resume not found or expired.")

        if isinstance(data, bytes):
            data = data.decode("utf-8")
            logger.debug(f"Decoded Redis data for resume_id {resume_id}")

        result = json.loads(data)
        logger.info(f"Parsed resume data for resume_id: {resume_id}")

        response = {
            "image_url": result.get("image_url"),
            "resume_url": result.get("resume_url"),
            "feedback": result.get("feedback"),
        }

        logger.info(f"Successfully retrieved feedback for resume_id: {resume_id}")
        return response

    except Exception as e:
        logger.exception("Error retrieving resume feedback")
        raise HTTPException(status_code=500, detail=f"Error retrieving data: {repr(e)}")


# GET: retrieve all resumes for authenticated user
@router.get("/user-resumes", response_model=list[dict])
async def get_user_resumes(request: Request):
    try:
        logger.info("Fetching resumes for authenticated user")

        user_details = authenticate_and_get_user_details(request)
        clerk_id = user_details.get("user_id")
        logger.info(f"Authenticated user with Clerk ID: {clerk_id}")

        resumes = []
        keys_scanned = 0

        # Use scan_iter for efficient iteration
        for key in redis_client.scan_iter("resume:*"):
            keys_scanned += 1
            data = redis_client.get(key)
            if not data:
                continue

            if isinstance(data, bytes):
                data = data.decode("utf-8")

            resume = json.loads(data)

            # Ensure key is string for split
            if isinstance(key, bytes):
                key = key.decode("utf-8")

            if resume.get("clerk_id") == clerk_id:
                resume_id = key.split(":")[-1]
                resumes.append({
                    "resume_id": resume_id,
                    "resume_url": resume.get("resume_url"),
                    "image_url": resume.get("image_url"),
                    "job_title": resume.get("job_title"),
                    "feedback": resume.get("feedback"),
                })

        logger.info(f"Scanned {keys_scanned} Redis keys. Found {len(resumes)} resumes for user {clerk_id}")

        # Fallback to MongoDB if Redis has nothing
        if not resumes:
            from models.user import User
            logger.info(f"No Redis cache found for user {clerk_id}. Checking MongoDB...")
            user = await User.find_one({"clerk_id": clerk_id})
            if user:
                logger.info(f"User data found in MongoDB for {clerk_id}")
                resumes.append({
                    "resume_id": str(user.id),
                    "resume_url": user.resume_url,
                    "image_url": user.image_url,
                    "job_title": user.job_title,
                    "feedback": user.feedback.dict() if user.feedback else {},
                })
            else:
                logger.info(f"No resumes found in MongoDB for user {clerk_id}")

        return resumes

    except Exception as e:
        logger.exception("Error retrieving user resumes")
        raise HTTPException(status_code=500, detail=f"Error: {repr(e)}")

