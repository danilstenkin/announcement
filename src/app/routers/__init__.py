from fastapi import APIRouter

from routers.announcements import router as announcements_router

router = APIRouter()
router.include_router(announcements_router)
