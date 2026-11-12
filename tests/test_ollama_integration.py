"""Opt-in tests against the locally running Ollama server and pulled models."""
import json
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import pytest

from timbot.db import Base
from timbot.models import KnowledgeDocument, Product, Workspace
from timbot.services.ai import TOOL_DEFINITIONS, answer, index_document, retrieve, _product_tool
from timbot.services.providers import get_provider

pytestmark = pytest.mark.skipif(
    os.getenv("TIMBOT_TEST_OLLAMA") != "1",
    reason="Set TIMBOT_TEST_OLLAMA=1 to run inference against local Ollama models",
)

def test_local_ollama_chat_and_embeddings():
    provider = get_provider()
    status = provider.health()
    assert status["available"], status["error"]
    assert status["chat_model_available"], f"Pull {provider.chat_model} first"
    assert status["embedding_model_available"], f"Pull {provider.embedding_model} first"

    chat = provider.chat(messages=[
        {"role": "system", "content": 'Return a JSON object with an "answer" string set to "local ok".'},
        {"role": "user", "content": "Confirm the local inference server is responding."},
    ], response_format={"type": "json_object"}, temperature=0, max_tokens=40)
    structured = json.loads(chat.choices[0].message.content)
    assert structured["answer"]

    vectors = provider.embed(["TIMBOT local embedding integration check."])
    assert len(vectors.vectors) == 1
    assert len(vectors.vectors[0]) == provider.embedding_dimensions

def test_local_ollama_product_retrieval_and_tenant_filter():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            alpha, beta = Workspace(name="Alpha"), Workspace(name="Beta")
            db.add_all([alpha, beta])
            db.flush()
            alpha_doc = KnowledgeDocument(workspace_id=alpha.id, filename="alpha.txt", media_type="text/plain", content="The ceramic mug is blue.")
            beta_doc = KnowledgeDocument(workspace_id=beta.id, filename="beta.txt", media_type="text/plain", content="The ceramic mug is red.")
            db.add_all([alpha_doc, beta_doc])
            db.flush()
            db.add_all([
                Product(workspace_id=alpha.id, name="Ceramic mug", description="Blue glazed mug", price_cents=1250, stock=8),
                Product(workspace_id=beta.id, name="Ceramic mug", description="Red glazed mug", price_cents=800, stock=2),
            ])
            db.commit()
            index_document(db, alpha_doc)
            index_document(db, beta_doc)
            matches = retrieve(db, alpha.id, "What color is the mug?")
            assert matches and all(chunk.workspace_id == alpha.id for chunk in matches)
            assert matches[0].content == "The ceramic mug is blue."
            products = _product_tool(db, alpha.id, "mug")
            assert len(products) == 1 and products[0]["price_cents"] == 1250 and products[0]["stock"] == 8
            handover = answer(db, alpha.id, "Can you check the status of order 123?", [])
            assert handover.needs_human
    finally:
        engine.dispose()

@pytest.mark.skipif(
    os.getenv("TIMBOT_TEST_OLLAMA_TOOLS") != "1",
    reason="Set TIMBOT_TEST_OLLAMA_TOOLS=1 to verify model-generated tool calls",
)
def test_local_ollama_model_can_call_inventory_tool():
    provider = get_provider()
    response = provider.chat(messages=[
        {"role": "system", "content": "You must call lookup_inventory for the Ceramic mug before replying. Do not guess stock."},
        {"role": "user", "content": "Is the Ceramic mug in stock?"},
    ], tools=TOOL_DEFINITIONS, tool_choice="auto", temperature=0, max_tokens=160)
    calls = response.choices[0].message.tool_calls or []
    assert any(call.function.name == "lookup_inventory" for call in calls), response.choices[0].message.content

def test_local_ollama_api_chatbot_acceptance():
    """Exercise signup through stored AI reply and manual handover against real Ollama."""
    from test_api import isolated_test_client, create_user

    provider = get_provider()
    assert provider.health()["available"]
    with isolated_test_client() as client:
        email, _ = create_user(client, "Local Ollama Shop")
        login = client.post("/api/auth/login", json={"email": email, "password": "a-secure-password"})
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}

        product = client.post("/api/products", headers=headers, json={
            "name": "Blue ceramic mug", "sku": "MUG-LOCAL-1",
            "description": "Handmade blue glazed ceramic mug", "price_cents": 2450, "stock": 6,
        })
        assert product.status_code == 201
        faq = client.post("/api/knowledge/import", headers=headers, files={
            "file": ("policies.txt", (
                "SHIPPING POLICY - Standard delivery takes 3-5 business days. "
                "RETURN POLICY - Products can be returned within 7 days of delivery. "
                "- Products must be unused and in original packaging. "
                "- Used or damaged products are not eligible for return. "
                "REFUND POLICY - Approved refunds are processed within 5-7 business days. "
                "- Refunds are sent to the original payment method."
            ), "text/plain"),
        })
        assert faq.status_code == 201
        indexed = client.post(f"/api/knowledge/{faq.json()['id']}/index", headers=headers)
        assert indexed.status_code == 200 and indexed.json()["indexed_chunks"] == 1

        conversation = client.post("/api/conversations", headers=headers, json={
            "customer_name": "Local integration customer", "channel": "web",
        })
        assert conversation.status_code == 201
        conversation_id = conversation.json()["id"]
        response = client.post(f"/api/conversations/{conversation_id}/messages", headers=headers, json={
            "body": "What is the price and stock for the Blue ceramic mug?",
        })
        assert response.status_code == 201
        actual_reply = response.json()["ai_reply"]
        assert actual_reply["answer"].startswith("Blue ceramic mug Handmade blue glazed ceramic mug"), actual_reply
        assert "Price: ₹24.50." in actual_reply["answer"]
        assert "USD" not in actual_reply["answer"]
        assert "Stock recorded: 6." in actual_reply["answer"]

        running_shoes = client.post("/api/products", headers=headers, json={
            "name": "Running Shoes", "sku": "RUN001",
            "description": "Lightweight running shoes available in sizes 7, 8, 9 and 10.",
            "price_cents": 179900, "stock": 15,
        })
        assert running_shoes.status_code == 201
        for question, expected in (
            ("Do you have Running Shoes in stock?", "In stock (15 available)"),
            ("What is the price of Running Shoes?", "₹1,799.00"),
            ("What is the SKU for Running Shoes?", "RUN001"),
            ("What sizes are available?", "sizes 7, 8, 9 and 10"),
        ):
            catalogue_response = client.post(f"/api/conversations/{conversation_id}/messages", headers=headers, json={"body": question})
            assert catalogue_response.status_code == 201
            assert catalogue_response.json()["ai_reply"]["needs_human"] is False
            assert expected in catalogue_response.json()["ai_reply"]["answer"]
            assert "Rs 49 delivery charge" not in catalogue_response.json()["ai_reply"]["answer"]

        for question, expected in (
            ("Describe them", "Lightweight running shoes available in sizes 7, 8, 9 and 10."),
            ("What is its price?", "₹1,799.00"),
            ("What sizes are available?", "sizes 7, 8, 9 and 10"),
            ("Are they still in stock?", "In stock (15 available)"),
        ):
            followup = client.post(f"/api/conversations/{conversation_id}/messages", headers=headers, json={"body": question})
            assert followup.status_code == 201
            assert followup.json()["ai_reply"]["needs_human"] is False
            assert expected in followup.json()["ai_reply"]["answer"]

        policy = client.post(f"/api/conversations/{conversation_id}/messages", headers=headers, json={
            "body": "What is the return policy?",
        })
        assert policy.status_code == 201
        return_answer = policy.json()["ai_reply"]["answer"]
        assert return_answer == (
            "You can return products within 7 days of delivery. Products must be unused and in their original packaging. "
            "Used or damaged products are not eligible for return."
        ), return_answer
        assert "SHIPPING POLICY" not in return_answer and "REFUND POLICY" not in return_answer

        for international_question in (
            "Do you offer international shipping to the USA?",
            "Do you ship outside India?",
        ):
            resume = client.patch(f"/api/conversations/{conversation_id}/mode", headers=headers, json={"ai_enabled": True})
            assert resume.status_code == 200 and resume.json()["ai_enabled"] is True
            international = client.post(f"/api/conversations/{conversation_id}/messages", headers=headers, json={
                "body": international_question,
            })
            assert international.status_code == 201
            answer = international.json()["ai_reply"]
            assert answer["needs_human"] is True
            assert "team member" in answer["answer"]
            assert "3-5 business days" not in answer["answer"]
            assert "Rs 999" not in answer["answer"]

        resume = client.patch(f"/api/conversations/{conversation_id}/mode", headers=headers, json={"ai_enabled": True})
        assert resume.status_code == 200 and resume.json()["ai_enabled"] is True
        missing_product = client.post(f"/api/conversations/{conversation_id}/messages", headers=headers, json={
            "body": "Do you have Nike shoes in stock?",
        })
        assert missing_product.status_code == 201
        assert missing_product.json()["ai_reply"]["needs_human"] is True
        assert "Rs 49 delivery charge" not in missing_product.json()["ai_reply"]["answer"]

        resume = client.patch(f"/api/conversations/{conversation_id}/mode", headers=headers, json={"ai_enabled": True})
        assert resume.status_code == 200 and resume.json()["ai_enabled"] is True
        unknown = client.post(f"/api/conversations/{conversation_id}/messages", headers=headers, json={
            "body": "What is the price of the Golden astronaut helmet?",
        })
        assert unknown.status_code == 201
        assert unknown.json()["ai_reply"]["needs_human"] is True
        assert "Golden astronaut helmet" not in unknown.json()["ai_reply"]["answer"]

        history = client.get(f"/api/conversations/{conversation_id}/messages", headers=headers)
        assert history.status_code == 200 and len(history.json()) == 28
        assert history.json()[1]["body"] == actual_reply["answer"]
        assert history.json()[1]["delivery_status"] == "stored"
        takeover = client.patch(f"/api/conversations/{conversation_id}/mode", headers=headers, json={"ai_enabled": False})
        assert takeover.status_code == 200 and takeover.json()["ai_enabled"] is False
        agent = client.post(f"/api/conversations/{conversation_id}/reply", headers=headers, json={"body": "An agent has taken over."})
        assert agent.status_code == 201 and agent.json()["author_type"] == "agent"
        assert agent.json()["delivery_status"] == "stored"
        returned = client.patch(f"/api/conversations/{conversation_id}/mode", headers=headers, json={"ai_enabled": True})
        assert returned.status_code == 200 and returned.json()["ai_enabled"] is True
