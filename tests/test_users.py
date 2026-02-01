import pytest


@pytest.mark.asyncio
async def test_read_me_returns_current_user(client, current_user):
    response = await client.get("/me")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == current_user["id"]
    assert data["email"] == current_user["email"]
    assert data["name"] == current_user["name"]
    assert "created_at" in data
