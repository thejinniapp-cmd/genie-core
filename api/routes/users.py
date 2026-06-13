"""api/routes/users.py"""
from fastapi import APIRouter
router = APIRouter()

@router.get("/")
def list_users(): return []
