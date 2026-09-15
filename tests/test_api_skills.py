"""
Tests: Cron CRUD, Skill CRUD, Memory Write, Session Content Search, Health.
"""
import json, pathlib, urllib.error, urllib.parse, urllib.request

from tests._test_helpers import get_body, post, make_session_tracked

# ── Health (Phase G) ──────────────────────────────────────────────

def test_health_has_active_streams():
    data = get("/health")
    assert "active_streams" in data
    assert isinstance(data["active_streams"], int) and data["active_streams"] >= 0

def test_health_has_uptime_seconds():
    data = get("/health")
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], (int, float)) and data["uptime_seconds"] >= 0

# ── Session content search ────────────────────────────────────────

def test_session_search_empty_returns_all(cleanup_test_sessions):
    data = get("/api/sessions/search?q=")
    assert "sessions" in data

def test_session_search_content_params_accepted(cleanup_test_sessions):
    data = get("/api/sessions/search?q=hello&content=1&depth=3")
    assert "sessions" in data and "query" in data and data["query"] == "hello"

def test_session_search_returns_count(cleanup_test_sessions):
    data = get("/api/sessions/search?q=nonexistent_xyz_9999&content=1")
    assert "count" in data and data["count"] == 0

# ── Cron update ───────────────────────────────────────────────────

def test_cron_update_requires_job_id(cleanup_test_sessions):
    data, status = post("/api/crons/update", {"name": "test"})
    assert status == 400

def test_cron_update_unknown_job_404(cleanup_test_sessions):
    data, status = post("/api/crons/update", {"job_id": "nonexistent_abc123"})
    assert status == 404

# ── Cron delete ───────────────────────────────────────────────────

def test_cron_delete_requires_job_id(cleanup_test_sessions):
    data, status = post("/api/crons/delete", {})
    assert status == 400

def test_cron_delete_unknown_404(cleanup_test_sessions):
    data, status = post("/api/crons/delete", {"job_id": "nonexistent_xyz999"})
    assert status == 404

# ── Skill save ────────────────────────────────────────────────────

def test_skill_save_requires_name(cleanup_test_sessions):
    data, status = post("/api/skills/save", {"content": "# test"})
    assert status == 400

def test_skill_save_requires_content(cleanup_test_sessions):
    data, status = post("/api/skills/save", {"name": "test-no-content"})
    assert status == 400

def test_skill_save_invalid_name_rejected(cleanup_test_sessions):
    data, status = post("/api/skills/save", {"name": "../../../etc/passwd", "content": "bad"})
    assert status == 400

def test_skill_save_delete_roundtrip(cleanup_test_sessions):
    skill_name = "test-sprint7-skill"
    content = "---\nname: test-sprint7-skill\ndescription: Sprint 7 test.\ntags: [test]\n---\n\n# Test\n\nSprint 7 test skill."
    data, status = post("/api/skills/save", {"name": skill_name, "content": content})
    assert status == 200 and data.get("ok") is True
    skill_path = pathlib.Path(data["path"])
    assert skill_path.exists() and skill_path.read_text() == content
    del_data, del_status = post("/api/skills/delete", {"name": skill_name})
    assert del_status == 200 and del_data.get("ok") is True
    assert not skill_path.exists()

def test_skill_delete_requires_name(cleanup_test_sessions):
    data, status = post("/api/skills/delete", {})
    assert status == 400

def test_skill_delete_unknown_404(cleanup_test_sessions):
    data, status = post("/api/skills/delete", {"name": "nonexistent-skill-xyz-9999"})
    assert status == 404

# ── Memory write ──────────────────────────────────────────────────

def test_memory_write_requires_section(cleanup_test_sessions):
    data, status = post("/api/memory/write", {"content": "test"})
    assert status == 400

def test_memory_write_requires_content(cleanup_test_sessions):
    data, status = post("/api/memory/write", {"section": "memory"})
    assert status == 400

def test_memory_write_invalid_section(cleanup_test_sessions):
    data, status = post("/api/memory/write", {"section": "invalid", "content": "test"})
    assert status == 400

def test_memory_write_read_roundtrip(cleanup_test_sessions):
    original = get("/api/memory").get("memory", "")
    test_content = "# Sprint 7 Test\nWritten by test_memory_write_read_roundtrip."
    data, status = post("/api/memory/write", {"section": "memory", "content": test_content})
    assert status == 200 and data.get("ok") is True
    read_back = get("/api/memory").get("memory")
    assert read_back == test_content
    # Restore
    post("/api/memory/write", {"section": "memory", "content": original})
