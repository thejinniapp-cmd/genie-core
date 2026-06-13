"""api/routes/rag.py"""
from fastapi import APIRouter
router = APIRouter()

@router.get("/")
def list_rag(): return []
