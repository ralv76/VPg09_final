"""Тесты REST API. Критерии 7.2: обработка ошибок, корректные ответы."""
import pytest


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("status") == "ok"


def test_extract_no_input(client):
    r = client.post("/api/extract", json={})
    assert r.status_code == 400
    data = r.get_json()
    assert "error" in data
    assert "recommendation" in data


def test_extract_url_empty(client):
    r = client.post("/api/extract", json={"url": ""})
    assert r.status_code == 400


def test_tasks_get_not_found(client):
    r = client.get("/api/tasks/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_music_list(client):
    r = client.get("/api/music")
    assert r.status_code == 200
    data = r.get_json()
    assert "tracks" in data
