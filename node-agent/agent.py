import asyncio
import logging
import secrets
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from api import router as api_router
from monitor import system_monitor as monitor  # shared singleton (same instance api.py uses)
from config import agent_settings, validate_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("node-agent")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Arqon Node Agent...")
    await monitor.start()
    yield
    logger.info("Shutting down Arqon Node Agent...")
    await monitor.stop()

app = FastAPI(
    title="Arqon Node Agent",
    description="Node agent for Arqon Host VPS Bot - VPS & Container Management",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # the agent is a machine-to-machine API — browsers never talk to it
    # directly; wildcard origins with credentials is an invalid/unsafe combo
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

security = HTTPBearer(auto_error=False)

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # constant-time comparison prevents timing attacks
    if not secrets.compare_digest(credentials.credentials, agent_settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials

app.include_router(api_router, prefix="/api/v1", dependencies=[Depends(verify_api_key)])

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "arqon-node-agent"}

@app.get("/")
async def root():
    return {"service": "Arqon Node Agent", "version": "2.0.0"}

def main():
    # Fail fast on misconfiguration instead of silently 401-ing every request.
    errors = validate_settings()
    if errors:
        for err in errors:
            logger.critical(f"Config error: {err}")
        sys.exit(1)
    logger.info("Starting Arqon Node Agent server...")
    uvicorn.run(
        "agent:app",
        host=agent_settings.HOST,
        port=agent_settings.PORT,
        reload=agent_settings.DEBUG,
        log_level="info",
    )

if __name__ == "__main__":
    main()