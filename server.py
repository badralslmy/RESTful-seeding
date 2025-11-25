from fastapi import FastAPI, File, UploadFile, BackgroundTasks, Form, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import os
import shutil
import uuid
import json
import logging
from typing import Optional
from seedrcc import Seedr
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Seedr credentials
SEEDR_EMAIL = os.getenv("SEEDR_EMAIL")
SEEDR_PASS = os.getenv("SEEDR_PASS")

if not SEEDR_EMAIL or not SEEDR_PASS:
    raise ValueError("SEEDR_EMAIL and SEEDR_PASS environment variables are required.")

seedr = Seedr(SEEDR_EMAIL, SEEDR_PASS)

# File-based storage for upload status
STATUS_DIR = "/tmp/upload_statuses"
os.makedirs(STATUS_DIR, exist_ok=True)

# --- Exception Handlers ---
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"An unhandled exception occurred: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "An internal server error occurred."},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


def write_status(file_id: str, status_data: dict):
    """Writes the upload status to a JSON file."""
    with open(os.path.join(STATUS_DIR, f"{file_id}.json"), "w") as f:
        json.dump(status_data, f)

def read_status(file_id: str) -> Optional[dict]:
    """Reads the upload status from a JSON file."""
    status_path = os.path.join(STATUS_DIR, f"{file_id}.json")
    if not os.path.exists(status_path):
        return None
    with open(status_path, "r") as f:
        return json.load(f)

def upload_to_seedr_in_background(file_id: str, tmp_path: str):
    """
    Uploads a file to Seedr in the background and cleans up the temporary file.
    """
    try:
        write_status(file_id, {"status": "processing"})
        result = seedr.upload_file(tmp_path)
        write_status(file_id, {"status": "completed", "result": result})
    except Exception as e:
        logger.error(f"Upload failed for file_id {file_id}: {e}")
        write_status(file_id, {"status": "failed", "error": "Upload process failed."})
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.get("/")
def read_root():
    return {"message": "Seedr Proxy API is running."}

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    """
    Uploads a file to Seedr with background processing.
    """
    file_id = str(uuid.uuid4())
    tmp_path = f"/tmp/{file_id}-{file.filename}"

    with open(tmp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    background_tasks.add_task(upload_to_seedr_in_background, file_id, tmp_path)
    write_status(file_id, {"status": "pending"})

    return JSONResponse(content={"file_id": file_id}, status_code=202)

@app.get("/upload/status/{file_id}")
async def get_upload_status(file_id: str):
    """
    Gets the upload status of a file.
    """
    status = read_status(file_id)
    if not status:
        return JSONResponse(content={"error": "File not found"}, status_code=404)
    return JSONResponse(content=status, status_code=200)

@app.post("/add")
async def add_torrent(magnet: str = Form(...)):
    """
    Adds a torrent to Seedr via a magnet link.
    """
    result = seedr.add_torrent(magnet)
    return JSONResponse(content=result, status_code=200)

@app.get("/list")
async def list_files():
    """
    Lists all files and folders in Seedr.
    """
    contents = seedr.list_contents()
    return JSONResponse(content=contents, status_code=200)

@app.get("/status")
async def get_status(file_id: Optional[str] = None):
    """
    Gets the status of a specific file or all files in Seedr.
    """
    if file_id:
        status = seedr.get_file(file_id)
    else:
        status = seedr.list_contents()
    return JSONResponse(content=status, status_code=200)

@app.delete("/items/{item_id}")
async def delete_item(item_id: str, item_type: str = "file"):
    """
    Deletes a file or folder from Seedr.
    """
    if item_type == "file":
        result = seedr.delete_file(item_id)
    elif item_type == "folder":
        result = seedr.delete_folder(item_id)
    else:
        return JSONResponse(content={"error": "Invalid item_type. Must be 'file' or 'folder'."}, status_code=400)
    return JSONResponse(content=result, status_code=200)
