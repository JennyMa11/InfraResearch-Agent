from sqlalchemy import create_engine, inspect, text

from infraresearch.database import _migrate_evidence_scores


def test_existing_sqlite_evidence_table_gains_separate_score_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE evidence ("
                "id VARCHAR(80) PRIMARY KEY, score FLOAT NOT NULL"
                ")"
            )
        )
        connection.execute(text("INSERT INTO evidence (id, score) VALUES ('run:S1', 0.75)"))

    _migrate_evidence_scores(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("evidence")}
    assert {"retrieval_score", "rerank_score"} <= columns
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT score, retrieval_score, rerank_score "
                "FROM evidence WHERE id = 'run:S1'"
            )
        ).one()
    assert row == (0.75, 0.75, None)
