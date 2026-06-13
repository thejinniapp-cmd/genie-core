"""api/routes/skills.py"""
from fastapi import APIRouter
router = APIRouter()

@router.get("/")
def list_skills(): return []
