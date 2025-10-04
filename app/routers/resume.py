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
        user_details = authenticate_and_get_user_details(request)
        clerk_id = user_details.get("user_id")

        resume_bytes = await resume.read()

        feedback_data = ResumeReviewGenerator.review_resume(
            file_bytes=resume_bytes,
            content_type=resume.content_type,
            job_title=jobTitle,
            job_description=jobDescription,
        )
        feedback_obj = Feedback(**feedback_data)

        resume_result = cloudinary.uploader.upload(
            resume_bytes,
            resource_type="image",
            public_id=f"resumes/{uuid.uuid4()}_{resume.filename}",
            type="upload"
        )
        resume_url = resume_result["secure_url"]

        image_url = resume_url.replace("/upload/", "/upload/pg_1,f_png/")

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

        # --- Save or update MongoDB ---
        user = await User.find_one({"clerk_id": clerk_id})
        if not user:
            user = User(
                clerk_id=clerk_id,
                resume_url=resume_url,
                image_url=image_url,
                job_title=jobTitle,
                job_description=jobDescription,
                feedback=feedback_obj,
            )
            await user.insert()
        else:
            user.resume_url = resume_url
            user.image_url = image_url
            user.job_title = jobTitle
            user.job_description = jobDescription
            user.feedback = feedback_obj
            user.updated_at = datetime.utcnow()
            await user.save()

        return {
            "id": resume_id,
            "resume_url": resume_url,
            "image_url": image_url,
            "message": "Resume analyzed, uploaded to Cloudinary, cached, and saved to DB.",
        }

    except Exception as e:
        logging.exception("Error analyzing resume")
        raise HTTPException(status_code=500, detail=f"Error analyzing resume: {e}")


# GET: retrieve cached feedback by ID
@router.get("/resume-feedback/{resume_id}", response_model=dict)
async def get_resume_feedback(resume_id: str):
    try:
        data = redis_client.get(f"resume:{resume_id}")
        if not data:
            raise HTTPException(status_code=404, detail="Resume not found or expired.")

        # Decode bytes if needed
        if isinstance(data, bytes):
            data = data.decode("utf-8")

        # Parse JSON
        result = json.loads(data)

        # Build response with only required fields
        response = {
            "image_url": result.get("image_url"),
            "resume_url": result.get("resume_url"),
            "feedback": result.get("feedback"),
        }

        return response

    except Exception as e:
        logger.exception("Error retrieving resume feedback")
        raise HTTPException(status_code=500, detail=f"Error retrieving data: {e}")


# GET: retrieve all resumes for authenticated user
@router.get("/user-resumes", response_model=list[dict])
async def get_user_resumes(request: Request):
    try:
        user_details = authenticate_and_get_user_details(request)
        clerk_id = user_details.get("user_id")

        resumes = []

        # Use scan_iter (non-blocking alternative to KEYS)
        for key in redis_client.scan_iter("resume:*"):
            data = redis_client.get(key)
            if not data:
                continue

            # Decode Redis data if needed
            if isinstance(data, bytes):
                data = data.decode("utf-8")

            resume = json.loads(data)

            # 🔧 Safely handle key whether it's bytes or str
            if isinstance(key, bytes):
                key = key.decode("utf-8")

            if resume.get("clerk_id") == clerk_id:
                resumes.append({
                    "resume_id": key.split(":")[-1],  # works now for both str and bytes
                    "resume_url": resume.get("resume_url"),
                    "image_url": resume.get("image_url"),
                    "job_title": resume.get("job_title"),
                    "feedback": resume.get("feedback"),
                })

        # If no cached resumes, fall back to MongoDB
        if not resumes:
            from models.user import User
            user = await User.find_one({"clerk_id": clerk_id})
            if user:
                resumes.append({
                    "resume_id": str(user.id),
                    "resume_url": user.resume_url,
                    "image_url": user.image_url,
                    "job_title": user.job_title,
                    "feedback": user.feedback.dict() if user.feedback else {},
                })

        return resumes

    except Exception as e:
        logger.exception("Error retrieving user resumes")
        raise HTTPException(status_code=500, detail=f"Error: {e}")

