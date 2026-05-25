from fastapi import APIRouter

from routers.announcements import router as announcements_router
from routers.auto_announce import router as auto_announce_router
from routers.tickets import router as tickets_router

router = APIRouter()
router.include_router(announcements_router)
router.include_router(auto_announce_router)
router.include_router(tickets_router)
