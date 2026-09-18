"""Contract tests for GET /api/slash-commands (T006a)."""
import json
import urllib.request

from tests.conftest import TEST_BASE, requires_agent_modules


def _get(path):
    """GET helper -- returns parsed JSON or raises HTTPError."""
    with urllib.request.urlopen(TEST_BASE + path, timeout=10) as r:
        return json.loads(r.read())


class TestSlashCommandsEndpoint:
    """Verify GET /api/slash-commands returns correctly shaped response."""

    def test_endpoint_returns_200(self):
        """GET /api/slash-commands returns HTTP 200."""
        with urllib.request.urlopen(TEST_BASE + "/api/slash-commands", timeout=10) as resp:
            assert resp.status == 200

    def test_response_structure(self):
        """Response has 'builtin', 'skills', 'plugins' arrays."""
        data = _get("/api/slash-commands")
        assert "builtin" in data
        assert "skills" in data
        assert "plugins" in data
        assert isinstance(data["builtin"], list)
        assert isinstance(data["skills"], list)
        assert isinstance(data["plugins"], list)

    @requires_agent_modules
    def test_builtin_commands_shape(self):
        """Each builtin entry has required fields."""
        data = _get("/api/slash-commands")
        builtins = data["builtin"]
        assert len(builtins) > 0, "Expected at least some built-in commands"
        for cmd in builtins:
            assert "name" in cmd
            assert "description" in cmd
            assert "category" in cmd
            # type field not required on builtins from api/commands.py,
            # but name must be a non-empty string
            assert isinstance(cmd["name"], str) and cmd["name"]

    @requires_agent_modules
    def test_skill_commands_shape(self):
        """Each skill entry has name, slug, description, category, dir_name."""
        data = _get("/api/slash-commands")
        skills = data["skills"]
        for sk in skills:
            assert "name" in sk
            assert "slug" in sk
            assert "description" in sk
            assert "dir_name" in sk
            # category can be null for root-level skills
            assert "category" in sk

    @requires_agent_modules
    def test_help_command_in_builtins(self):
        """/help must be present in built-in commands."""
        data = _get("/api/slash-commands")
        names = {c["name"] for c in data["builtin"]}
        assert "help" in names, "/help must be in built-in command list"
