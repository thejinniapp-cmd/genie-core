"""api/routes/jobs.py"""
from fastapi import APIRouter
router = APIRouter()

@router.get("/")
def list_jobs(): return []
