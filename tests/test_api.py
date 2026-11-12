import uuid
import json
from types import SimpleNamespace
import pytest
from contextlib import contextmanager
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from timbot.db import Base, get_db
from timbot.main import app
from timbot import models  # noqa: F401

@contextmanager
def isolated_test_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    def get_test_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = get_test_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()

def create_user(client, name):
    email = f"{name.lower().replace(' ', '-')}--{uuid.uuid4().hex}@example.com"
    response = client.post("/api/auth/signup", json={"email": email, "password": "a-secure-password", "workspace_name": name})
    assert response.status_code == 201
    return email, {"Authorization": "Bearer " + response.json()["access_token"]}

def test_signup_login_workspace_and_tenant_scoped_product_crud():
    with isolated_test_client() as client:
        email_a, ah = create_user(client, "Alpha")
        _, bh = create_user(client, "Beta")
        login = client.post("/api/auth/login", json={"email": email_a, "password": "a-secure-password"})
        assert login.status_code == 200
        product = client.post("/api/products", headers=ah, json={"name": "Alpha item", "price_cents": 1200, "stock": 3})
        assert product.status_code == 201
        pid = product.json()["id"]
        price_product = client.post("/api/products", headers=ah, json={"name": "INR price check", "price_cents": 179900})
        assert price_product.status_code == 201
        assert price_product.json()["price_cents"] == 179900
        assert price_product.json()["currency"] == "INR"
        assert client.get(f"/api/products/{price_product.json()['id']}", headers=ah).json()["price_cents"] == 179900
        assert len(client.get("/api/products", headers=ah).json()) == 2
        assert client.get("/api/products", headers=bh).json() == []
        assert client.get(f"/api/products/{pid}", headers=bh).status_code == 404
        updated = client.patch(f"/api/products/{pid}", headers=ah, json={"name": "Updated", "sku": "A1", "description": "new", "price_cents": 1400, "stock": 2})
        assert updated.json()["name"] == "Updated"
        assert client.delete(f"/api/products/{pid}", headers=ah).status_code == 204
        assert client.get("/api/workspace", headers=bh).json()["name"] == "Beta"

def test_product_lookup_is_workspace_scoped():
    from timbot.services.ai import _product_tool
    with isolated_test_client() as client:
        _, alpha_headers = create_user(client, "Product Alpha")
        _, beta_headers = create_user(client, "Product Beta")
        client.post("/api/products", headers=alpha_headers, json={
            "name": "Running Shoes", "sku": "RUN001", "description": "Lightweight running shoes available in sizes 7, 8, 9 and 10.", "price_cents": 179900, "stock": 15,
        })
        client.post("/api/products", headers=beta_headers, json={
            "name": "Running Shoes", "sku": "SECRET-RUN", "description": "Different tenant product.", "price_cents": 1, "stock": 99,
        })
        db_dependency = app.dependency_overrides[get_db]()
        db = next(db_dependency)
        try:
            alpha_workspace = client.get("/api/workspace", headers=alpha_headers).json()["id"]
            result = _product_tool(db, alpha_workspace, "running shoes")
            assert len(result) == 1
            assert result[0]["sku"] == "RUN001"
            assert result[0]["stock"] == 15
        finally:
            db.close()

def test_catalogue_questions_do_not_use_policy_retrieval_and_missing_products_fallback(monkeypatch):
    from timbot.services import ai

    class NoInferenceProvider:
        def embed(self, texts):
            raise AssertionError("Catalogue fact requests should not run policy retrieval")
        def chat(self, **kwargs):
            raise AssertionError("Catalogue fact requests should use grounded catalogue data directly")

    monkeypatch.setattr(ai, "provider", lambda: NoInferenceProvider())
    with isolated_test_client() as client:
        _, headers = create_user(client, "Catalogue routing")
        product = client.post("/api/products", headers=headers, json={
            "name": "Running Shoes", "sku": "RUN001",
            "description": "Lightweight running shoes available in sizes 7, 8, 9 and 10.",
            "price_cents": 179900, "stock": 15,
        })
        assert product.status_code == 201
        # A nearby domestic policy must never be consulted for catalogue facts.
        faq = client.post("/api/knowledge/import", headers=headers, files={
            "file": ("shipping.txt", "Orders below Rs 999 have a Rs 49 delivery charge.", "text/plain"),
        })
        assert faq.status_code == 201
        conversation = client.post("/api/conversations", headers=headers, json={"customer_name": "Buyer", "channel": "web"}).json()

        for question, expected in [
            ("Do you have Running Shoes in stock?", "In stock (15 available)"),
            ("What is the price of Running Shoes?", "₹1,799.00"),
            ("What is the SKU for Running Shoes?", "RUN001"),
            ("What sizes are available?", "sizes 7, 8, 9 and 10"),
        ]:
            response = client.post(f"/api/conversations/{conversation['id']}/messages", headers=headers, json={"body": question})
            assert response.status_code == 201
            result = response.json()["ai_reply"]
            assert result["needs_human"] is False
            assert expected in result["answer"]
            assert "Rs 49 delivery charge" not in result["answer"]

        for question, expected in [
            ("Describe them", "Lightweight running shoes available in sizes 7, 8, 9 and 10."),
            ("What is its price?", "₹1,799.00"),
            ("What sizes are available?", "sizes 7, 8, 9 and 10"),
            ("Are they still in stock?", "In stock (15 available)"),
            ("Tell me more", "Lightweight running shoes available in sizes 7, 8, 9 and 10."),
        ]:
            response = client.post(f"/api/conversations/{conversation['id']}/messages", headers=headers, json={"body": question})
            assert response.status_code == 201
            result = response.json()["ai_reply"]
            assert result["needs_human"] is False
            assert expected in result["answer"]
            assert "Rs 49 delivery charge" not in result["answer"]

        unknown = client.post(f"/api/conversations/{conversation['id']}/messages", headers=headers, json={
            "body": "Do you have Nike shoes in stock?",
        })
        assert unknown.status_code == 201
        assert unknown.json()["ai_reply"]["needs_human"] is True
        assert "Rs 49 delivery charge" not in unknown.json()["ai_reply"]["answer"]

        new_conversation = client.post("/api/conversations", headers=headers, json={"customer_name": "New buyer", "channel": "web"}).json()
        unresolved = client.post(f"/api/conversations/{new_conversation['id']}/messages", headers=headers, json={"body": "Describe it"})
        assert unresolved.status_code == 201
        assert unresolved.json()["ai_reply"]["needs_human"] is True

def test_contextual_product_reference_rejects_ambiguous_history():
    from timbot.services.ai import _resolve_contextual_product
    products = [
        {"name": "Running Shoes", "sku": "RUN001"},
        {"name": "Hiking Boots", "sku": "HIKE001"},
    ]
    history = [{"role": "assistant", "content": "Running Shoes and Hiking Boots are in stock."}]
    product, ambiguous = _resolve_contextual_product("Describe them", history, products)
    assert product is None and ambiguous is True
    product, ambiguous = _resolve_contextual_product("Describe it", [], products)
    assert product is None and ambiguous is False

def test_auth_and_human_handover(monkeypatch):
    from timbot.services.providers.base import AIProviderUnavailableError
    from timbot.services import ai
    class UnavailableProvider:
        def embed(self, texts): raise AIProviderUnavailableError("mock local model unavailable")
    monkeypatch.setattr(ai, "provider", lambda: UnavailableProvider())
    with isolated_test_client() as client:
        assert client.get("/api/products").status_code == 401
        email, headers = create_user(client, "Support")
        assert client.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401
        conversation = client.post("/api/conversations", headers=headers, json={"customer_name": "Taylor", "channel": "web"}).json()
        cid = conversation["id"]
        blocked = client.post(f"/api/conversations/{cid}/messages", headers=headers, json={"body": "What is your return policy?"})
        assert blocked.status_code == 201 and blocked.json()["ai_reply"]["needs_human"] is True
        assert len(client.get(f"/api/conversations/{cid}/messages", headers=headers).json()) == 2
        takeover = client.patch(f"/api/conversations/{cid}/mode", headers=headers, json={"ai_enabled": False})
        assert takeover.json()["ai_enabled"] is False
        reply = client.post(f"/api/conversations/{cid}/reply", headers=headers, json={"body": "How can I help?"})
        assert reply.status_code == 201 and reply.json()["author_type"] == "agent"
        history = client.get(f"/api/conversations/{cid}/messages", headers=headers).json()
        assert len(history) == 3
        assert [row["direction"] for row in history] == ["inbound", "outbound", "outbound"]

def test_web_outbound_status_is_local_and_legacy_history_is_preserved():
    from timbot.main import outbound_delivery_status
    from timbot.models import Message
    assert outbound_delivery_status("web") == "stored"
    assert outbound_delivery_status("messenger") == "queued"
    with isolated_test_client() as client:
        _, headers = create_user(client, "Legacy history")
        conversation = client.post("/api/conversations", headers=headers, json={"customer_name": "Local", "channel": "web"}).json()
        workspace_id = client.get("/api/workspace", headers=headers).json()["id"]
        db_dependency = app.dependency_overrides[get_db]()
        db = next(db_dependency)
        try:
            db.add(Message(workspace_id=workspace_id, conversation_id=conversation["id"], direction="outbound", body="Existing reply", delivery_status="queued", author_type="ai"))
            db.commit()
        finally:
            db.close()
        history = client.get(f"/api/conversations/{conversation['id']}/messages", headers=headers).json()
        assert history[0]["body"] == "Existing reply"
        assert history[0]["delivery_status"] == "queued"
import io
from openpyxl import Workbook

def test_product_csv_and_xlsx_ingestion():
    with isolated_test_client() as client:
        _, headers = create_user(client, "Importer")
        csv_file = ("products.csv", "name,sku,price,stock\nMug,M-1,12.50,8\n", "text/csv")
        response = client.post("/api/products/import", headers=headers, files={"file": csv_file})
        assert response.status_code == 201 and response.json()["imported"] == 1
        row = client.get("/api/products", headers=headers).json()[0]
        assert row["name"] == "Mug" and row["price_cents"] == 1250 and row["stock"] == 8

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["name", "sku", "price_cents", "stock"])
        sheet.append(["Lamp", "L-2", 5500, 4])
        content = io.BytesIO()
        workbook.save(content)
        response = client.post("/api/products/import", headers=headers, files={"file": ("products.xlsx", content.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert response.status_code == 201 and response.json()["imported"] == 1
        names = {product["name"] for product in client.get("/api/products", headers=headers).json()}
        assert names == {"Mug", "Lamp"}

def test_knowledge_document_ingest_is_tenant_scoped():
    with isolated_test_client() as client:
        _, owner = create_user(client, "Docs")
        _, other = create_user(client, "Other")
        response = client.post("/api/knowledge/import", headers=owner, files={"file": ("returns.txt", "Returns are accepted within 30 days.", "text/plain")})
        assert response.status_code == 201
        doc_id = response.json()["id"]
        assert len(client.get("/api/knowledge", headers=owner).json()) == 1
        assert client.get("/api/knowledge", headers=other).json() == []
        assert client.delete(f"/api/knowledge/{doc_id}", headers=other).status_code == 404

def test_mocked_grounded_chatbot_end_to_end(monkeypatch):
    from timbot.services import ai
    from timbot.services.providers.base import EmbeddingBatch

    class FakeProvider:
        name = "ollama"
        chat_model = "qwen3:8b"
        embedding_model = "nomic-embed-text"
        embedding_profile = "ollama:nomic-embed-text:768"
        def embed(self, texts):
            return EmbeddingBatch([[1.0] + [0.0] * 767 for _ in texts], 5, 2)
        def chat(self, **kwargs):
            if not hasattr(self, "captured"):
                self.captured = []
            self.captured.append(kwargs)
            if len(self.captured) == 1:
                call = SimpleNamespace(id="call-product", function=SimpleNamespace(name="lookup_product", arguments='{"query":"mug"}'))
                message = SimpleNamespace(tool_calls=[call], content=None, model_dump=lambda **kwargs: {"role":"assistant", "tool_calls":[]})
                return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1))
            message = SimpleNamespace(tool_calls=None, content=json.dumps({
                    "answer": "The ceramic mug is $12.50 with 8 in stock. Returns are accepted within 30 days.",
                    "intent": "product", "needs_human": False, "reason": None, "confidence": "high",
                    "evidence": ["Returns are accepted within 30 days of delivery."],
                }))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=40, completion_tokens=18, total_tokens=58))

    fake = FakeProvider()
    monkeypatch.setattr(ai, "provider", lambda: fake)

    with isolated_test_client() as client:
        email, _ = create_user(client, "Grounded shop")
        login = client.post("/api/auth/login", json={"email": email, "password": "a-secure-password"})
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}

        catalogue = client.post("/api/products/import", headers=headers, files={
            "file": ("catalog.csv", "name,sku,description,price,stock\nCeramic mug,MUG-1,Blue handmade mug,12.50,8\n", "text/csv"),
        })
        assert catalogue.status_code == 201
        from timbot.services.ai import _execute_tool
        db = next(app.dependency_overrides[get_db]())
        try:
            workspace_id = client.get("/api/workspace", headers=headers).json()["id"]
            inventory = _execute_tool(db, workspace_id, "lookup_inventory", '{"query":"mug"}')
            assert inventory["inventory"][0]["stock"] == 8
            order = _execute_tool(db, workspace_id, "lookup_order", '{"order_id":"ORDER-1"}')
            assert order["available"] is False and order["needs_human"] is True
        finally:
            db.close()
        faq = client.post("/api/knowledge/import", headers=headers, files={
            "file": ("returns.txt", "Returns are accepted within 30 days of delivery.", "text/plain"),
        })
        assert faq.status_code == 201
        indexed = client.post(f"/api/knowledge/{faq.json()['id']}/index", headers=headers)
        assert indexed.status_code == 200 and indexed.json()["indexed_chunks"] == 1

        conversation = client.post("/api/conversations", headers=headers, json={"customer_name": "Test customer", "channel": "web"}).json()
        reply = client.post(f"/api/conversations/{conversation['id']}/messages", headers=headers, json={"body": "What does the mug cost and what is the return policy?"})
        assert reply.status_code == 201
        assert reply.json()["ai_reply"]["answer"].startswith("Ceramic mug Blue handmade mug Price: ₹12.50.")
        assert "Returns are accepted within 30 days of delivery." in reply.json()["ai_reply"]["answer"]
        captured_prompt = str(fake.captured[0]["messages"])
        assert '"price_cents": 1250' in captured_prompt and "Returns are accepted within 30 days" in captured_prompt
        assert '"name": "Ceramic mug"' in str(fake.captured[1]["messages"])

        history = client.get(f"/api/conversations/{conversation['id']}/messages", headers=headers).json()
        assert [row["direction"] for row in history] == ["inbound", "outbound"]
        assert history[1]["author_type"] == "ai"
        assert history[1]["body"] == reply.json()["ai_reply"]["answer"]
        assert history[1]["delivery_status"] == "stored"
        stats = client.get("/api/analytics/summary", headers=headers).json()
        assert stats["ai_responses"] == 1 and stats["ai_token_usage"] == 69
        assert stats["ai_average_latency_ms"] > 0

        mode = client.patch(f"/api/conversations/{conversation['id']}/mode", headers=headers, json={"ai_enabled": False})
        assert mode.json()["ai_enabled"] is False
        agent = client.post(f"/api/conversations/{conversation['id']}/reply", headers=headers, json={"body": "A teammate can help further."})
        assert agent.status_code == 201 and agent.json()["author_type"] == "agent"
        assert agent.json()["delivery_status"] == "stored"
        mode = client.patch(f"/api/conversations/{conversation['id']}/mode", headers=headers, json={"ai_enabled": True})
        assert mode.json()["ai_enabled"] is True

def test_ollama_provider_rejects_openai_host_and_pins_local_url():
    from timbot.services.providers.ollama import OllamaProvider
    config = SimpleNamespace(
        ollama_base_url="http://localhost:11434/v1", ollama_chat_model="qwen3:8b",
        ollama_embedding_model="nomic-embed-text", embedding_dimensions=768,
        ai_request_timeout_seconds=30, ai_max_retries=2,
    )
    provider = OllamaProvider(config)
    try:
        assert str(provider._client.base_url) == "http://localhost:11434/v1/"
    finally:
        provider._client.close()
    with pytest.raises(ValueError, match="cannot use an OpenAI server URL"):
        OllamaProvider(SimpleNamespace(**{**vars(config), "ollama_base_url": "https://api.openai.com/v1"}))

def test_embedding_dimension_mismatch_is_rejected_before_reindex():
    from timbot.services.ai import _validate_dimensions, EmbeddingDimensionError, GroundedReply, _ground_business_answer
    with pytest.raises(EmbeddingDimensionError, match="returned 2 dimensions"):
        _validate_dimensions([[0.1, 0.2]], 768, 1)
    unsafe = GroundedReply(answer="It costs $9.99", intent="greeting", needs_human=False, reason=None, confidence="high")
    safe = _ground_business_answer(unsafe, "What is the price?", [], [], False)
    assert safe.needs_human and "$9.99" not in safe.answer
    greeting = GroundedReply(answer="ignored", intent="greeting", needs_human=False, reason=None, confidence="high")
    assert _ground_business_answer(greeting, "Hello!", [], [], False).answer == "Hi! How can I help you today?"

def test_product_ai_grounding_formats_paise_as_inr_only():
    from timbot.services.ai import GroundedReply, _ground_business_answer
    answer = _ground_business_answer(
        GroundedReply(answer="This is USD 1,799.00", intent="product", needs_human=False, reason=None, confidence="high"),
        "What is the price of the mug?",
        [{"name": "Ceramic mug", "description": "Blue glazed mug", "price_cents": 179900, "stock": 8}],
        [],
        False,
    )
    assert "₹1,799.00" in answer.answer
    assert "USD" not in answer.answer

@pytest.mark.parametrize(("question", "expected", "intent"), [
    (
        "What is your return policy?",
        "You can return products within 7 days of delivery. Products must be unused and in their original packaging. Used or damaged products are not eligible for return.",
        "returns",
    ),
    (
        "How long does shipping take?",
        "Standard delivery takes 3-5 business days.",
        "shipping",
    ),
    (
        "When will I get my refund?",
        "Approved refunds are processed within 5-7 business days. Refunds are sent to the original payment method.",
        "policy",
    ),
])
def test_policy_answers_are_relevant_and_never_echo_retrieved_document(question, expected, intent):
    from timbot.services.ai import GroundedReply, _ground_business_answer, _relevant_answer_facts

    document = SimpleNamespace(content=(
        "BUSINESS: TIMBOT Demo Store SHIPPING POLICY - Standard delivery takes 3-5 business days. "
        "- Free shipping on orders above Rs 999. - Orders below Rs 999 have a Rs 49 delivery charge. "
        "RETURN POLICY - Products can be returned within 7 days of delivery. "
        "- Products must be unused and in original packaging. - Used or damaged products are not eligible for return. "
        "REFUND POLICY - Approved refunds are processed within 5-7 business days. "
        "- Refunds are sent to the original payment method. PAYMENT METHODS - UPI and cards are accepted."
    ))
    facts = _relevant_answer_facts(question, [document])
    # Deliberately give grounding an unsafe model response to prove it ignores
    # a whole-document dump and uses only the requested, supported facts.
    unsafe = GroundedReply(
        answer=document.content, intent=intent, needs_human=False, reason=None,
        confidence="high", evidence=[document.content],
    )
    grounded = _ground_business_answer(unsafe, question, [], [document], False, facts)
    assert grounded.answer == expected
    assert "BUSINESS:" not in grounded.answer
    assert "PAYMENT METHODS" not in grounded.answer
    assert grounded.needs_human is False

def test_unknown_policy_uses_human_fallback_without_exposing_document():
    from timbot.services.ai import GroundedReply, _ground_business_answer, _relevant_answer_facts

    document = SimpleNamespace(content="RETURN POLICY - Products can be returned within 7 days.")
    question = "What is your warranty policy?"
    facts = _relevant_answer_facts(question, [document])
    unsafe = GroundedReply(
        answer=document.content, intent="policy", needs_human=False, reason=None,
        confidence="high", evidence=[document.content],
    )
    grounded = _ground_business_answer(unsafe, question, [], [document], False, facts)
    assert grounded.needs_human is True
    assert "team member" in grounded.answer
    assert "RETURN POLICY" not in grounded.answer

@pytest.mark.parametrize(("question", "expected"), [
    ("How long does delivery take?", "Standard delivery takes 3-5 business days."),
    ("Is shipping free above Rs 999?", "Shipping is free on orders above Rs 999."),
])
def test_domestic_shipping_answers_use_only_matching_domestic_facts(question, expected):
    from timbot.services.ai import GroundedReply, _ground_business_answer, _relevant_answer_facts

    document = SimpleNamespace(content=(
        "SHIPPING POLICY - Standard delivery takes 3-5 business days. "
        "- Free shipping on orders above Rs 999. - Orders below Rs 999 have a Rs 49 delivery charge."
    ))
    facts = _relevant_answer_facts(question, [document])
    grounded = _ground_business_answer(
        GroundedReply(answer="irrelevant source dump", intent="shipping", needs_human=False, reason=None, confidence="high"),
        question, [], [document], False, facts,
    )
    assert grounded.answer == expected
    assert grounded.needs_human is False

@pytest.mark.parametrize(("question"), [
    "Do you offer international shipping to the USA?",
    "Do you ship outside India?",
])
def test_international_shipping_does_not_use_domestic_evidence(question):
    from timbot.services.ai import GroundedReply, _ground_business_answer, _relevant_answer_facts

    document = SimpleNamespace(content=(
        "SHIPPING POLICY - Standard delivery takes 3-5 business days. "
        "- Free shipping on domestic orders above Rs 999. - Orders below Rs 999 have a Rs 49 delivery charge."
    ))
    facts = _relevant_answer_facts(question, [document])
    grounded = _ground_business_answer(
        GroundedReply(
            answer="Standard delivery takes 3-5 business days. Shipping is free above Rs 999.",
            intent="shipping", needs_human=False, reason=None, confidence="high",
            evidence=[document.content],
        ), question, [], [document], False, facts,
    )
    assert facts == []
    assert grounded.needs_human is True
    assert "team member" in grounded.answer
    assert "3-5 business days" not in grounded.answer
    assert "Rs 999" not in grounded.answer

def test_mocked_ollama_rag_retrieval_is_workspace_scoped(monkeypatch):
    from timbot.services import ai
    from timbot.services.providers.base import EmbeddingBatch

    class FakeProvider:
        embedding_profile = "ollama:nomic-embed-text:768"
        def embed(self, texts):
            return EmbeddingBatch([[1.0] + [0.0] * 767 for _ in texts], 1, 1)

    monkeypatch.setattr(ai, "provider", lambda: FakeProvider())
    with isolated_test_client() as client:
        _, owner = create_user(client, "RAG owner")
        _, other = create_user(client, "RAG other")
        doc_ids = []
        for headers, filename, body in [
            (owner, "owner.txt", "Owner return policy: 21 days."),
            (other, "other.txt", "Other return policy: 90 days."),
        ]:
            upload = client.post("/api/knowledge/import", headers=headers, files={"file": (filename, body, "text/plain")})
            assert upload.status_code == 201
            doc_id = upload.json()["id"]
            assert client.post(f"/api/knowledge/{doc_id}/index", headers=headers).status_code == 200
            doc_ids.append(doc_id)
        db_dependency = app.dependency_overrides[get_db]()
        db = next(db_dependency)
        try:
            owner_workspace = client.get("/api/workspace", headers=owner).json()["id"]
            chunks = ai.retrieve(db, owner_workspace, "return policy")
            assert len(chunks) == 1 and chunks[0].document_id == doc_ids[0]
            assert chunks[0].document_id != doc_ids[1]
        finally:
            db.close()

def test_ollama_health_reports_missing_models_without_leaking_credentials(monkeypatch):
    from timbot.services.providers.ollama import OllamaProvider
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"models": [{"name": "qwen3:8b"}]}
    monkeypatch.setattr("timbot.services.providers.ollama.httpx.get", lambda *args, **kwargs: Response())
    provider = OllamaProvider(SimpleNamespace(
        ollama_base_url="http://localhost:11434/v1", ollama_chat_model="qwen3:8b",
        ollama_embedding_model="nomic-embed-text", embedding_dimensions=768,
        ai_request_timeout_seconds=30, ai_max_retries=2,
    ))
    try:
        status = provider.health()
        assert status["available"] is True
        assert status["chat_model_available"] is True
        assert status["embedding_model_available"] is False
        assert "api_key" not in str(status).lower()
    finally:
        provider._client.close()

