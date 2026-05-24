from sqlalchemy import text


def test_engine_connects(engine):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1


def test_get_session_yields_working_session(session):
    result = session.execute(text("SELECT 1"))
    assert result.scalar() == 1


def test_session_rollback_isolation(session):
    session.execute(text("CREATE TEMP TABLE test_isolation (val int)"))
    session.execute(text("INSERT INTO test_isolation VALUES (42)"))
    result = session.execute(text("SELECT val FROM test_isolation")).scalar()
    assert result == 42
