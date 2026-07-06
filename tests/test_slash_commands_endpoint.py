"""Contract tests for GET /api/slash-commands (T006a)."""
from pathlib import Path
import json
import urllib.request

ROOT = Path(__file__).parent.parent


class TestSlashCommandsEndpoint:
    """Verify GET /api/slash-commands returns correctly shaped response."""

    def test_endpoint_returns_200(self, test_server):
        """GET /api/slash-commands returns HTTP 200."""
        url = f"http://127.0.0.1:{test_server}/api/slash-commands"
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req)
        assert resp.status == 200

    def test_response_structure(self, test_server):
        """Response has 'builtin', 'skills', 'plugins' arrays."""
        url = f"http://127.0.0.1:{test_server}/api/slash-commands"
        data = json.loads(urllib.request.urlopen(url).read())
        assert "builtin" in data
        assert "skills" in data
        assert "plugins" in data
        assert isinstance(data["builtin"], list)
        assert isinstance(data["skills"], list)
        assert isinstance(data["plugins"], list)

    def test_builtin_commands_shape(self, test_server):
        """Each builtin entry has required fields."""
        url = f"http://127.0.0.1:{test_server}/api/slash-commands"
        data = json.loads(urllib.request.urlopen(url).read())
        builtins = data["builtin"]
        assert len(builtins) > 0, "Expected at least some built-in commands"
        for cmd in builtins:
            assert "name" in cmd
            assert "description" in cmd
            assert "category" in cmd
            # type field not required on builtins from api/commands.py,
            # but name must be a non-empty string
            assert isinstance(cmd["name"], str) and cmd["name"]

    def test_skill_commands_shape(self, test_server):
        """Each skill entry has name, slug, description, category, dir_name."""
        url = f"http://127.0.0.1:{test_server}/api/slash-commands"
        data = json.loads(urllib.request.urlopen(url).read())
        skills = data["skills"]
        for sk in skills:
            assert "name" in sk
            assert "slug" in sk
            assert "description" in sk
            assert "dir_name" in sk
            # category can be null for root-level skills
            assert "category" in sk

    def test_help_command_in_builtins(self, test_server):
        """/help must be present in built-in commands."""
        url = f"http://127.0.0.1:{test_server}/api/slash-commands"
        data = json.loads(urllib.request.urlopen(url).read())
        names = {c["name"] for c in data["builtin"]}
        assert "help" in names, "/help must be in built-in command list"
