"""
run_agent.py — Runner del PromptAgent
Ejecutar con: python run_agent.py

Corre el loop de polling que procesa mensajes del stream con Claude.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from agents.prompt_agent import PromptAgent

if __name__ == "__main__":
    agent = PromptAgent()
    agent.main()
