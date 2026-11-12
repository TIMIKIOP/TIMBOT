from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pwdlib import PasswordHash
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import csv
import io
import re
import zipfile
from timbot.config import settings
from timbot.db import Base, engine, get_db
from timbot.models import AIUsage, Workspace, User, Membership, Product, Conversation, KnowledgeDocument, KnowledgeChunk, Message

passwords = PasswordHash.recommended()
bearer = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

def token_for(uid: int, workspace_id: int) -> str:
    return jwt.encode({"sub": str(uid), "wid": workspace_id, "exp": datetime.now(timezone.utc) + timedelta(hours=12)}, settings.app_secret_key, algorithm="HS256")

def membership(token: str = Depends(bearer), db: Session = Depends(get_db)) -> Membership:
    try:
        claims = jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])
        uid = int(claims["sub"])
        wid = int(claims["wid"]) if claims.get("wid") is not None else None
    except (JWTError, ValueError, KeyError):
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    query = select(Membership).where(Membership.user_id == uid)
    if wid is not None:
        query = query.where(Membership.workspace_id == wid)
    row = db.scalar(query.order_by(Membership.id))
    if row is None:
        raise HTTPException(403, "No workspace membership")
    return row

def require_admin(m: Membership = Depends(membership)) -> Membership:
    if m.role not in {"owner", "admin"}:
        raise HTTPException(403, "Workspace administrator role required")
    return m

class Signup(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=128)
    workspace_name: str = Field(min_length=2, max_length=160)
class Login(BaseModel):
    email: str
    password: str
class WorkspaceInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: str | None = Field(default=None, max_length=100)
    description: str = Field(default="", max_length=10000)
    price_cents: int | None = Field(default=None, ge=0, description="Price in paise (100 paise = ₹1); currency is INR.")
    stock: int | None = Field(default=None, ge=0)

@asynccontextmanager
async def lifespan(app):
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    if settings.app_env.lower() == "production":
        if settings.app_secret_key == "development-only-change-me" or len(settings.app_secret_key) < 32:
            raise RuntimeError("APP_SECRET_KEY must be a unique secret of at least 32 characters in production")
    if settings.app_env.lower() == "development":
        Base.metadata.create_all(engine)
    yield

app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.origins, allow_credentials=True, allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

@app.get("/health")
def health():
    return {"status": "ok", "service": "timbot-api"}

def ai_health_payload():
    from timbot.services.ai import provider
    try:
        current = provider()
    except HTTPException as exc:
        return {"provider": settings.ai_provider, "available": False, "chat_model_available": False, "embedding_model_available": False, "error": exc.detail}
    return {
        "provider": current.name,
        "chat_model": current.chat_model,
        "embedding_model": current.embedding_model,
        **current.health(),
    }

@app.get("/health/ai")
def ai_health():
    return ai_health_payload()

@app.post("/api/auth/signup", status_code=201)
def signup(data: Signup, db: Session = Depends(get_db)):
    email = data.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Invalid email")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    user = User(email=email, password_hash=passwords.hash(data.password))
    workspace = Workspace(name=data.workspace_name.strip())
    db.add_all([user, workspace])
    db.flush()
    membership_row = Membership(user_id=user.id, workspace_id=workspace.id, role="owner")
    db.add(membership_row)
    db.commit()
    return {"access_token": token_for(user.id, workspace.id), "token_type": "bearer"}

@app.post("/api/auth/login")
def login(data: Login, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
    if not user or not passwords.verify(data.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    member = db.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.id))
    if member is None:
        raise HTTPException(403, "No workspace membership")
    return {"access_token": token_for(user.id, member.workspace_id), "token_type": "bearer"}

@app.post("/api/auth/logout")
def logout():
    # JWTs are stateless; the client must discard this access token.
    return {"success": True, "message": "Discard the bearer token to sign out."}

@app.get("/api/auth/me")
def whoami(m: Membership = Depends(membership)):
    return {"id": m.user.id, "email": m.user.email, "role": m.role, "workspace_id": m.workspace_id}

@app.get("/api/workspaces")
def list_workspaces(db: Session = Depends(get_db), m: Membership = Depends(membership)):
    rows = db.scalars(select(Membership).where(Membership.user_id == m.user_id).order_by(Membership.id))
    return [{"id": row.workspace.id, "name": row.workspace.name, "role": row.role} for row in rows]

@app.post("/api/workspaces", status_code=201)
def create_workspace(data: WorkspaceInput, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    workspace = Workspace(name=data.name.strip())
    db.add(workspace)
    db.flush()
    db.add(Membership(user_id=m.user_id, workspace_id=workspace.id, role="owner"))
    db.commit()
    db.refresh(workspace)
    return {"id": workspace.id, "name": workspace.name, "role": "owner"}

@app.post("/api/auth/workspace/{workspace_id}")
def select_workspace(workspace_id: int, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    selected = db.scalar(select(Membership).where(Membership.user_id == m.user_id, Membership.workspace_id == workspace_id))
    if selected is None:
        raise HTTPException(404, "Workspace not found")
    return {"access_token": token_for(m.user_id, workspace_id), "token_type": "bearer"}

@app.get("/api/workspace")
def workspace(m: Membership = Depends(membership)):
    return {"id": m.workspace.id, "name": m.workspace.name, "role": m.role}

@app.patch("/api/workspace")
def rename_workspace(data: WorkspaceInput, db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    m.workspace.name = data.name.strip()
    db.commit()
    return {"id": m.workspace.id, "name": m.workspace.name, "role": m.role}

def validate_xlsx_package(raw: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        entries = package.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 50 * 1024 * 1024:
            raise ValueError("XLSX expands beyond the allowed archive limits")

def product_dict(p: Product):
    return {"id": p.id, "name": p.name, "sku": p.sku, "description": p.description, "price_cents": p.price_cents, "currency": "INR", "stock": p.stock}

@app.get("/api/products")
def products(db: Session = Depends(get_db), m: Membership = Depends(membership)):
    rows = db.scalars(select(Product).where(Product.workspace_id == m.workspace_id).order_by(Product.id.desc()))
    return [product_dict(row) for row in rows]

@app.post("/api/products", status_code=201)
def add_product(data: ProductInput, db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    product = Product(workspace_id=m.workspace_id, **data.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product_dict(product)

@app.get("/api/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    product = db.scalar(select(Product).where(Product.id == product_id, Product.workspace_id == m.workspace_id))
    if product is None:
        raise HTTPException(404, "Product not found")
    return product_dict(product)

@app.patch("/api/products/{product_id}")
def update_product(product_id: int, data: ProductInput, db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    product = db.scalar(select(Product).where(Product.id == product_id, Product.workspace_id == m.workspace_id))
    if product is None:
        raise HTTPException(404, "Product not found")
    for key, value in data.model_dump().items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product_dict(product)

@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    product = db.scalar(select(Product).where(Product.id == product_id, Product.workspace_id == m.workspace_id))
    if product is None:
        raise HTTPException(404, "Product not found")
    db.delete(product)
    db.commit()

@app.post("/api/products/import", status_code=201)
async def import_products(file: UploadFile = File(...), db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    filename = (file.filename or "").lower()
    if not filename.endswith((".csv", ".xlsx")):
        raise HTTPException(415, "Import a CSV or XLSX product sheet. PDF files are accepted by /api/knowledge/import.")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Upload exceeds the 10 MB limit")
    try:
        if filename.endswith(".csv"):
            rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        else:
            from openpyxl import load_workbook
            book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = book.active
            values = sheet.iter_rows(values_only=True)
            headers = [str(value or "").strip().lower() for value in next(values, ())]
            rows = [dict(zip(headers, row)) for row in values]
            book.close()
    except Exception as exc:
        raise HTTPException(422, f"Could not parse product sheet: {exc.__class__.__name__}")
    imported = []
    for index, source in enumerate(rows, start=2):
        row = {str(k or "").strip().lower(): v for k, v in source.items()}
        name = str(row.get("name") or row.get("product") or "").strip()
        if not name:
            raise HTTPException(422, f"Row {index} has no product name; nothing imported")
        try:
            raw_price = row.get("price_cents")
            price = int(raw_price) if raw_price not in (None, "") else (round(float(row["price"]) * 100) if row.get("price") not in (None, "") else None)
            stock = int(row["stock"]) if row.get("stock") not in (None, "") else None
            if price is not None and price < 0 or stock is not None and stock < 0:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, f"Row {index} has an invalid price or stock value; nothing imported")
        imported.append(Product(workspace_id=m.workspace_id, name=name[:200], sku=str(row.get("sku") or "")[:100] or None, description=str(row.get("description") or "")[:10000], price_cents=price, stock=stock))
        if len(imported) > 5000:
            raise HTTPException(413, "A single import is limited to 5,000 products")
    if not imported:
        raise HTTPException(422, "The uploaded file has no product rows")
    db.add_all(imported)
    db.commit()
    return {"imported": len(imported)}

@app.get("/api/knowledge")
def list_knowledge(db: Session = Depends(get_db), m: Membership = Depends(membership)):
    rows = db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == m.workspace_id).order_by(KnowledgeDocument.id.desc()))
    profile = f"{settings.ai_provider}:{settings.ollama_embedding_model}:{settings.embedding_dimensions}"
    result = []
    for row in rows:
        chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == row.id, KnowledgeChunk.workspace_id == m.workspace_id))
        indexed_chunks = sum(chunk.embedding_model == profile and chunk.embedding is not None for chunk in chunks)
        result.append({"id": row.id, "filename": row.filename, "media_type": row.media_type, "created_at": row.created_at.isoformat(), "indexed_chunks": indexed_chunks, "needs_reindex": indexed_chunks == 0})
    return result

@app.post("/api/knowledge/import", status_code=201)
async def import_knowledge(file: UploadFile = File(...), db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    filename = file.filename or "upload"
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension not in {"txt", "pdf", "csv", "xlsx"}:
        raise HTTPException(415, "Supported file types are TXT, PDF, CSV, and XLSX")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Upload exceeds the 10 MB limit")
    try:
        if extension == "txt":
            content = raw.decode("utf-8-sig")
        elif extension == "csv":
            content = "\n".join(",".join(str(cell or "") for cell in row) for row in csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
        elif extension == "xlsx":
            validate_xlsx_package(raw)
            from openpyxl import load_workbook
            book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            content = "\n".join(" | ".join(str(cell or "") for cell in row) for row in book.active.iter_rows(values_only=True))
            book.close()
        else:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if len(reader.pages) > 200:
                raise ValueError("PDF exceeds the 200 page limit")
            pieces = []
            total_chars = 0
            for page in reader.pages:
                piece = page.extract_text() or ""
                total_chars += len(piece)
                if total_chars > 1_000_000:
                    raise ValueError("Extracted PDF text exceeds the 1 MB limit")
                pieces.append(piece)
            content = "\n".join(pieces)
    except Exception as exc:
        raise HTTPException(422, f"Could not parse document: {exc.__class__.__name__}")
    content = content.strip()
    if not content:
        raise HTTPException(422, "No readable text found in this file")
    if len(content) > 1_000_000:
        raise HTTPException(413, "Extracted document exceeds the 1 MB text limit")
    doc = KnowledgeDocument(workspace_id=m.workspace_id, filename=filename[:255], media_type=file.content_type or "application/octet-stream", content=content)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "filename": doc.filename, "characters": len(content)}

@app.delete("/api/knowledge/{document_id}", status_code=204)
def delete_knowledge(document_id: int, db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, KnowledgeDocument.workspace_id == m.workspace_id))
    if doc is None:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()

@app.get("/api/conversations")
def conversations(db: Session = Depends(get_db), m: Membership = Depends(membership)):
    rows = db.scalars(select(Conversation).where(Conversation.workspace_id == m.workspace_id).order_by(Conversation.id.desc()))
    return [{"id": c.id, "channel": c.channel, "customer_name": c.customer_name, "status": c.status, "ai_enabled": c.ai_enabled} for c in rows]

class ConversationCreate(BaseModel):
    customer_name: str = Field(default="Customer", min_length=1, max_length=160)
    channel: str = Field(default="web", pattern="^web$")
class MessageInput(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
class ModeInput(BaseModel):
    ai_enabled: bool

def outbound_delivery_status(channel: str) -> str:
    """Local web chat persists messages without an external delivery step."""
    return "stored" if channel == "web" else "queued"

@app.post("/api/conversations", status_code=201)
def create_conversation(data: ConversationCreate, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    conversation = Conversation(workspace_id=m.workspace_id, channel=data.channel, customer_name=data.customer_name)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return {"id": conversation.id, "channel": conversation.channel, "customer_name": conversation.customer_name, "status": conversation.status, "ai_enabled": conversation.ai_enabled}

@app.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: int, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.workspace_id == m.workspace_id))
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.workspace_id == m.workspace_id).order_by(Message.id))
    return [{"id": row.id, "direction": row.direction, "body": row.body, "delivery_status": row.delivery_status, "author_type": row.author_type, "created_at": row.created_at.isoformat()} for row in rows]

@app.post("/api/conversations/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: int, data: MessageInput, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.workspace_id == m.workspace_id))
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    inbound = Message(workspace_id=m.workspace_id, conversation_id=conversation_id, direction="inbound", body=data.body, delivery_status="received", author_type="customer")
    db.add(inbound)
    db.commit()
    reply = None
    if conversation.ai_enabled:
        from timbot.services.ai import answer
        history_rows = db.scalars(select(Message).where(Message.workspace_id == m.workspace_id, Message.conversation_id == conversation_id).order_by(Message.id.desc()).limit(9))
        history = [{"role": "assistant" if row.direction in {"outbound", "manual"} else "user", "content": row.body} for row in list(reversed(list(history_rows)))[:-1]]
        result = answer(db, m.workspace_id, data.body, history)
        reply = result.model_dump()
        if result.needs_human:
            conversation.ai_enabled = False
            conversation.status = "pending"
        if result.answer:
            db.add(Message(workspace_id=m.workspace_id, conversation_id=conversation_id, direction="outbound", body=result.answer, delivery_status=outbound_delivery_status(conversation.channel), author_type="ai"))
        db.commit()
    db.refresh(inbound)
    return {"message": {"id": inbound.id, "direction": inbound.direction, "body": inbound.body, "delivery_status": inbound.delivery_status}, "ai_reply": reply, "ai_enabled": conversation.ai_enabled, "status": conversation.status}

@app.patch("/api/conversations/{conversation_id}/mode")
def set_conversation_mode(conversation_id: int, data: ModeInput, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.workspace_id == m.workspace_id))
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    conversation.ai_enabled = data.ai_enabled
    if data.ai_enabled and conversation.status == "pending":
        conversation.status = "open"
    db.commit()
    return {"id": conversation.id, "ai_enabled": conversation.ai_enabled, "status": conversation.status}

@app.patch("/api/conversations/{conversation_id}/status")
def set_conversation_status(conversation_id: int, status: str, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    if status not in {"open", "pending", "resolved"}:
        raise HTTPException(422, "Status must be open, pending, or resolved")
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.workspace_id == m.workspace_id))
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    conversation.status = status
    db.commit()
    return {"id": conversation.id, "status": conversation.status}

@app.post("/api/knowledge/{document_id}/index")
def index_knowledge(document_id: int, db: Session = Depends(get_db), m: Membership = Depends(require_admin)):
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, KnowledgeDocument.workspace_id == m.workspace_id))
    if document is None:
        raise HTTPException(404, "Document not found")
    if settings.redis_url:
        from timbot.worker import index_knowledge_document
        task = index_knowledge_document.delay(m.workspace_id, document_id)
        return {"document_id": document.id, "state": "queued", "task_id": task.id}
    from timbot.services.ai import index_document
    from timbot.services.providers.base import AIProviderError
    try:
        chunk_count = index_document(db, document)
    except AIProviderError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"document_id": document.id, "indexed_chunks": chunk_count}

@app.get("/api/ai/status")
def ai_status(m: Membership = Depends(membership)):
    health = ai_health_payload()
    configured = health.get("available", False) and health.get("chat_model_available", False) and health.get("embedding_model_available", False)
    model_configured = bool(health.get("chat_model") and health.get("embedding_model"))
    return {**health, "configured": configured, "model_configured": model_configured, "workspace_id": m.workspace_id}


class ManualReply(BaseModel):
    body: str = Field(min_length=1, max_length=10000)

@app.post("/api/conversations/{conversation_id}/reply", status_code=201)
def manual_reply(conversation_id: int, data: ManualReply, db: Session = Depends(get_db), m: Membership = Depends(membership)):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.workspace_id == m.workspace_id))
    if conversation is None:
        raise HTTPException(404, "Conversation not found")
    if conversation.ai_enabled:
        raise HTTPException(409, "Take over the conversation before sending a manual reply")
    message = Message(workspace_id=m.workspace_id, conversation_id=conversation_id, direction="outbound", body=data.body, delivery_status=outbound_delivery_status(conversation.channel), author_type="agent")
    db.add(message)
    db.commit()
    db.refresh(message)
    return {"id": message.id, "direction": message.direction, "body": message.body, "author_type": message.author_type, "delivery_status": message.delivery_status}

@app.get("/api/analytics/summary")
def analytics(db: Session = Depends(get_db), m: Membership = Depends(membership)):
    conversations = list(db.scalars(select(Conversation).where(Conversation.workspace_id == m.workspace_id)))
    messages = list(db.scalars(select(Message).where(Message.workspace_id == m.workspace_id).order_by(Message.created_at, Message.id)))
    response_delays = []
    waiting = defaultdict(deque)
    for message in messages:
        if message.author_type == "customer":
            waiting[message.conversation_id].append(message.created_at)
        elif message.author_type in {"ai", "agent"} and waiting[message.conversation_id]:
            first_waiting = waiting[message.conversation_id].popleft()
            response_delays.append(max(0, (message.created_at - first_waiting).total_seconds()))
    return {
        "conversations": len(conversations),
        "open_conversations": sum(c.status == "open" for c in conversations),
        "pending_conversations": sum(c.status == "pending" for c in conversations),
        "resolved_conversations": sum(c.status == "resolved" for c in conversations),
        "incoming_messages": sum(message.author_type == "customer" for message in messages),
        "ai_responses": sum(message.author_type == "ai" for message in messages),
        "human_responses": sum(message.author_type == "agent" for message in messages),
        "unanswered_messages": sum(len(queue) for queue in waiting.values()),
        "average_response_seconds": round(sum(response_delays) / len(response_delays), 1) if response_delays else None,
        "ai_average_latency_ms": round(db.scalar(select(func.avg(AIUsage.latency_ms)).where(AIUsage.workspace_id == m.workspace_id)) or 0, 1),
        "conversions": None,
        "conversions_tracking_enabled": False,
        "ai_token_usage": db.scalar(select(func.coalesce(func.sum(AIUsage.token_count), 0)).where(AIUsage.workspace_id == m.workspace_id)),
    }
from collections import defaultdict, deque






