"""
Tests: delegate child sessions carry a title, and the sidebar window is wide
enough for a supervised development run (issue #57).

The Hermes SDK never titles a subagent session — its auto-titler skips the
``subagent`` platform — yet the WebUI lists every delegate child in the
sidebar.  A supervised run (the ``supervise-fix-issue`` skill) forks one
child per phase and per retry, so the sidebar fills up with unnamed
children that cannot be told apart.

The WebUI therefore derives the name from the delegation goal, keeps it on
the session object, and writes it to state.db so it survives a reload.

Files touched by the production fix (mirrors this test's assertions):
  api/models.py:derive_child_title / persist_child_title / set_child_title
  api/models.py:Session.copy_from (an empty source title must not wipe it)
  api/models.py:_all_sessions_from_db (the 250-row sidebar window)
  api/streaming.py:subagent.start (name the child) and _close_child_stream
  frontend/src/components/sidebar/SessionItem.tsx (render the child's title)
"""

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api import models  # noqa: E402


REPO = pathlib.Path(__file__).resolve().parent.parent.parent


class _FakeSessionDB:
    """Minimal stand-in for the SDK's SessionDB title API.

    Mirrors hermes_state.SessionDB.set_auto_title(): it raises ValueError
    when another session already holds the title, and returns False when the
    row does not exist or a higher-authority title already holds it.
    """

    TITLE_SOURCE_DERIVED = 'derived'

    def __init__(self, taken=(), row_exists=True, held_title=None):
        self.taken = set(taken)
        self.row_exists = row_exists
        self.held_title = held_title
        self.writes = []

    def set_auto_title(self, session_id, title, *, source=None):
        self.writes.append((session_id, title, source))
        if not self.row_exists:
            return False
        if self.held_title:
            # A higher-authority title already holds the row.
            return False
        if title in self.taken:
            raise ValueError(
                "Title '{}' is already in use by another session".format(title)
            )
        self.taken.add(title)
        self.held_title = title
        return True

    def get_session_title(self, session_id):
        return self.held_title


def _session(session_id='child-1', title=''):
    return models.Session(session_id=session_id, title=title)


# ──────────────────────────────────────────────
# Title derivation
# ──────────────────────────────────────────────

def test_derive_child_title_uses_the_first_line_of_the_goal():
    assert models.derive_child_title('Fix the login bug') == 'Fix the login bug'
    derived = models.derive_child_title(
        'Fix the login bug\n\nThe session expired before the redirect.'
    )
    assert derived == 'Fix the login bug'


def test_derive_child_title_clips_a_long_goal():
    goal = 'x' * 500
    derived = models.derive_child_title(goal)
    assert derived.startswith('x' * models.CHILD_TITLE_MAX_LEN)
    assert derived.endswith('...')
    assert len(derived) <= models.CHILD_TITLE_MAX_LEN + len('...')


def test_derive_child_title_falls_back_to_a_generic_name():
    assert models.derive_child_title('') == 'Subagent task'
    assert models.derive_child_title('   ') == 'Subagent task'
    assert models.derive_child_title(None) == 'Subagent task'


def test_derive_child_title_leaves_room_for_the_uniqueness_suffix():
    # The SDK caps a title at 100 characters; the suffix appended on a
    # conflict must still fit.
    assert models.CHILD_TITLE_MAX_LEN + len(' (abcdefgh)') <= 100


# ──────────────────────────────────────────────
# Naming the child
# ──────────────────────────────────────────────

def test_set_child_title_names_the_session(monkeypatch):
    monkeypatch.setattr(models, '_get_session_db', lambda: None)
    session = _session()
    title = models.set_child_title(session, 'child-1', 'Write the migration')
    assert title == 'Write the migration'
    assert session.title == 'Write the migration'
    assert session._delegate_title == 'Write the migration'


def test_set_child_title_persists_the_title(monkeypatch):
    db = _FakeSessionDB()
    monkeypatch.setattr(models, '_get_session_db', lambda: db)
    session = _session()
    models.set_child_title(session, 'child-1', 'Write the migration')
    assert db.writes[0][0] == 'child-1'
    assert db.writes[0][1] == 'Write the migration'
    assert db.writes[0][2] == 'derived'


def test_persist_child_title_writes_through_set_auto_title(monkeypatch):
    db = _FakeSessionDB()
    monkeypatch.setattr(models, '_get_session_db', lambda: db)
    session = _session()
    session._delegate_title = 'Run the test suite'
    assert models.persist_child_title(session, 'child-1') is True
    assert session.title == 'Run the test suite'
    assert db.writes == [('child-1', 'Run the test suite', 'derived')]


def test_persist_child_title_retries_with_a_unique_suffix(monkeypatch):
    db = _FakeSessionDB(taken={'Run the test suite'})
    monkeypatch.setattr(models, '_get_session_db', lambda: db)
    session = _session()
    session._delegate_title = 'Run the test suite'
    assert models.persist_child_title(session, 'child-1') is True
    assert db.writes[0][1] == 'Run the test suite'
    assert db.writes[1][1] == 'Run the test suite (child-1)'
    assert session.title == 'Run the test suite (child-1)'


def test_persist_child_title_is_a_noop_without_a_derived_title(monkeypatch):
    db = _FakeSessionDB()
    monkeypatch.setattr(models, '_get_session_db', lambda: db)
    session = _session()
    assert models.persist_child_title(session, 'child-1') is False
    assert db.writes == []


def test_persist_child_title_is_a_noop_without_a_database(monkeypatch):
    monkeypatch.setattr(models, '_get_session_db', lambda: None)
    session = _session()
    session._delegate_title = 'Run the test suite'
    assert models.persist_child_title(session, 'child-1') is False
    # The in-memory title survives — the sidebar still shows it.
    assert session.title == ''


def test_persist_child_title_tolerates_a_missing_row(monkeypatch):
    # The SDK creates the child's row inside the child thread, so the row may
    # not exist yet when subagent.start fires. A False return is fine; the
    # write is retried when the child closes.
    db = _FakeSessionDB(row_exists=False)
    monkeypatch.setattr(models, '_get_session_db', lambda: db)
    session = _session()
    session._delegate_title = 'Run the test suite'
    assert models.persist_child_title(session, 'child-1') is False


def test_persist_child_title_survives_a_database_error(monkeypatch):
    class _Boom:
        TITLE_SOURCE_DERIVED = 'derived'

        def set_auto_title(self, *args, **kwargs):
            raise RuntimeError('state.db is locked')

    monkeypatch.setattr(models, '_get_session_db', lambda: _Boom())
    session = _session()
    session._delegate_title = 'Run the test suite'
    assert models.persist_child_title(session, 'child-1') is False


def test_a_title_the_user_typed_outranks_the_derived_one(monkeypatch):
    db = _FakeSessionDB(held_title='My own name')
    monkeypatch.setattr(models, '_get_session_db', lambda: db)
    session = _session()
    session._delegate_title = 'Run the test suite'
    assert models.persist_child_title(session, 'child-1') is False
    assert session.title == 'My own name'


def test_set_child_title_keeps_a_user_rename(monkeypatch):
    db = _FakeSessionDB(held_title='My own name')
    monkeypatch.setattr(models, '_get_session_db', lambda: db)
    session = _session()
    assert models.set_child_title(session, 'child-1', 'Run the test suite') == (
        'My own name'
    )


# ──────────────────────────────────────────────
# The title must survive a session refresh
# ──────────────────────────────────────────────

def test_copy_from_does_not_wipe_a_title_with_an_empty_one():
    target = _session(title='Write the migration')
    source = _session(title='')
    target.copy_from(source)
    assert target.title == 'Write the migration'


def test_copy_from_still_copies_a_real_title():
    target = _session(title='Old name')
    source = _session(title='New name')
    target.copy_from(source)
    assert target.title == 'New name'


# ──────────────────────────────────────────────
# The sidebar window
# ──────────────────────────────────────────────

def test_the_session_list_window_is_250():
    assert models._SESSION_LIST_LIMIT == 250


def test_the_session_list_window_reaches_the_sdk():
    class _RecordingDB:
        def __init__(self):
            self.kwargs = None

        def list_sessions_rich(self, **kwargs):
            self.kwargs = kwargs
            return []

    db = _RecordingDB()
    models._all_sessions_from_db(db, set())
    assert db.kwargs is not None
    assert db.kwargs['limit'] == 250
    assert db.kwargs['source'] == 'webui'


# ──────────────────────────────────────────────
# The wiring in the streaming layer and the UI
# ──────────────────────────────────────────────

def test_the_streaming_layer_names_the_child():
    source = (REPO / 'backend' / 'app' / 'api' / 'streaming.py').read_text()
    assert 'set_child_title' in source, (
        'the subagent.start handler must name the delegate child'
    )
    assert 'persist_child_title' in source, (
        'the child close handler must persist the title once the row exists'
    )


def test_the_sidebar_renders_the_child_title():
    source = (REPO / 'frontend' / 'src' / 'components' / 'sidebar'
              / 'SessionItem.tsx').read_text()
    assert 'child.title' in source, (
        'the delegate child item must render the child title'
    )


@pytest.mark.parametrize('relpath', ['docker-compose.yml', '.env'])
def test_the_concurrency_limit_is_250(relpath):
    text = (REPO / relpath).read_text()
    assert 'DELEGATION_MAX_CONCURRENT_CHILDREN' in text
    for line in text.splitlines():
        if 'DELEGATION_MAX_CONCURRENT_CHILDREN' in line and '=' in line:
            value = line.split('=', 1)[1].strip()
            value = value.strip('"').strip("'")
            value = value.split(':-')[-1].strip('}').strip()
            assert value == '250', (
                '{} pins the delegation limit to {}, expected 250'.format(
                    relpath, value)
            )
            break
    else:
        pytest.fail('{} does not set DELEGATION_MAX_CONCURRENT_CHILDREN'.format(relpath))
