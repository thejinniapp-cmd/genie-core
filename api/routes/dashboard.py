"""api/routes/dashboard.py"""
from fastapi import APIRouter
router = APIRouter()

@router.get("/")
def list_dashboard(): return []
