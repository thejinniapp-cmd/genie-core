from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import anthropic
import os

router = APIRouter()
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

class ChatMessage(BaseModel):
    role: str
    content: str

class OnboardingChatRequest(BaseModel):
    messages: List[ChatMessage]
    system: str

@router.post("/chat")
async def onboarding_chat(req: OnboardingChatRequest):
    """Conversational onboarding endpoint — Claude guides the user through setup."""
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]

    response = client.messages.create(
        model=os.environ.get("GENIE_DEFAULT_MODEL", "claude-sonnet-4-5"),
        max_tokens=1024,
        system=req.system,
        messages=msgs,
    )

    reply = response.content[0].text if response.content else ""
    return {"response": reply}
