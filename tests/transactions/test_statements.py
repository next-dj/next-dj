import pytest

from next.transactions.statements import StatementKind, statement_kind


class TestStatementKind:
    """The leading keyword classifies a statement, an unknown one counts as a write."""

    @pytest.mark.parametrize(
        ("sql", "kind"),
        [
            ("BEGIN", StatementKind.CONTROL),
            ("BEGIN;", StatementKind.CONTROL),
            ("BEGIN IMMEDIATE", StatementKind.CONTROL),
            ("BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE", StatementKind.CONTROL),
            ("START TRANSACTION", StatementKind.CONTROL),
            ("COMMIT", StatementKind.CONTROL),
            ("COMMIT PREPARED 'x'", StatementKind.CONTROL),
            ("END", StatementKind.CONTROL),
            ("ABORT", StatementKind.CONTROL),
            ("ROLLBACK", StatementKind.CONTROL),
            ('ROLLBACK TO SAVEPOINT "s1_x1"', StatementKind.CONTROL),
            ('RELEASE SAVEPOINT "s1_x1"', StatementKind.CONTROL),
            ('SAVEPOINT "s1_x1"', StatementKind.SESSION),
            ("SET CONSTRAINTS ALL IMMEDIATE", StatementKind.SESSION),
            ("RESET ALL", StatementKind.SESSION),
            ("SET LOCAL statement_timeout = 5000", StatementKind.SESSION),
            ("SET sql_mode = CONCAT(@@sql_mode, ',STRICT')", StatementKind.SESSION),
            ("SET @note = 'a call(' /* f( */", StatementKind.SESSION),
            ("PRAGMA foreign_keys = ON", StatementKind.SESSION),
            ("PRAGMA main.foreign_keys = OFF", StatementKind.SESSION),
            ("PRAGMA table_info(auth_group)", StatementKind.SESSION),
            ("pragma /* note */ busy_timeout = 20", StatementKind.SESSION),
            ("DISCARD TEMP", StatementKind.SESSION),
            ('SELECT "auth_group"."name" FROM "auth_group"', StatementKind.READ),
            ("  (SELECT 1) UNION (SELECT 2)", StatementKind.READ),
            ("/* app:view */ SELECT 1", StatementKind.READ),
            ("-- a note\nSELECT 1", StatementKind.READ),
            ("select updated_at from t", StatementKind.READ),
            ("select update from t", StatementKind.READ),
            ('SELECT "blog_post"."share" FROM "blog_post"', StatementKind.READ),
            ("SELECT `update` FROM t", StatementKind.READ),
            ("SELECT * FROM t WHERE note = 'please UPDATE me'", StatementKind.READ),
            ("SELECT * FROM t WHERE note = 'it''s FOR UPDATE'", StatementKind.READ),
            ("SELECT $$ FOR UPDATE $$", StatementKind.READ),
            ("SELECT 1 /* FOR UPDATE */", StatementKind.READ),
            ("SHOW search_path", StatementKind.READ),
            ("EXPLAIN SELECT 1", StatementKind.READ),
            ("WITH x AS (SELECT 1) SELECT * FROM x", StatementKind.READ),
            ("VALUES (1), (2)", StatementKind.READ),
            ("TABLE t", StatementKind.READ),
            ("DESCRIBE t", StatementKind.READ),
            ("DESC t", StatementKind.READ),
            (b"SELECT 1", StatementKind.READ),
            ('INSERT INTO "auth_group" ("name") VALUES (%s)', StatementKind.WRITE),
            ('UPDATE "auth_group" SET "name" = %s', StatementKind.WRITE),
            ('DELETE FROM "auth_group"', StatementKind.WRITE),
            ("CREATE TABLE t (id int)", StatementKind.WRITE),
            ("CALL refresh()", StatementKind.WRITE),
            ("SET @done = purge_all()", StatementKind.WRITE),
            ("SET @done = 'unclosed purge(", StatementKind.WRITE),
            ("PRAGMA user_version = 5", StatementKind.WRITE),
            ("PRAGMA main.application_id = 7", StatementKind.WRITE),
            ("PRAGMA journal_mode = WAL", StatementKind.WRITE),
            ("PRAGMA optimize", StatementKind.WRITE),
            ("PRAGMA wal_checkpoint(TRUNCATE)", StatementKind.WRITE),
            ('PRAGMA "main".foreign_keys = ON', StatementKind.WRITE),
            ("PRAGMA", StatementKind.WRITE),
            ("PRAGMA foreign_keys = ON; DELETE FROM t", StatementKind.WRITE),
            ("SELECT * FROM t FOR UPDATE", StatementKind.WRITE),
            ("SELECT * FROM t FOR NO KEY UPDATE SKIP LOCKED", StatementKind.WRITE),
            ("SELECT * FROM t FOR SHARE", StatementKind.WRITE),
            ("SELECT * FROM t FOR KEY SHARE", StatementKind.WRITE),
            ("SELECT * FROM t LOCK IN SHARE MODE", StatementKind.WRITE),
            ("SELECT * INTO copy FROM t", StatementKind.WRITE),
            ("WITH gone AS (DELETE FROM t RETURNING *) SELECT 1", StatementKind.WRITE),
            (
                "WITH u AS (UPDATE t SET a = 1 RETURNING *) SELECT 1",
                StatementKind.WRITE,
            ),
            ("EXPLAIN ANALYZE UPDATE t SET a = 1", StatementKind.WRITE),
            ("SELECT 'never closed FOR UPDATE", StatementKind.WRITE),
            ("BEGIN INSERT INTO t VALUES (1); END;", StatementKind.WRITE),
            ("DECLARE x NUMBER; BEGIN NULL; END;", StatementKind.WRITE),
            ("BEGIN 'never closed", StatementKind.WRITE),
            ("", StatementKind.WRITE),
            ("/* never closed SELECT 1", StatementKind.WRITE),
            ("-- SELECT 1", StatementKind.WRITE),
            (b"\xff", StatementKind.WRITE),
            (object(), StatementKind.WRITE),
            ("VACUUM", StatementKind.STANDALONE),
            ("VACUUM INTO 'copy.db'", StatementKind.STANDALONE),
            ("ATTACH DATABASE 'x.db' AS x", StatementKind.STANDALONE),
            ("DETACH DATABASE x", StatementKind.STANDALONE),
            ("CREATE DATABASE app", StatementKind.STANDALONE),
            ("DROP DATABASE app", StatementKind.STANDALONE),
            ("CREATE TABLESPACE fast LOCATION '/ssd'", StatementKind.STANDALONE),
            ("CREATE INDEX CONCURRENTLY i ON t (a)", StatementKind.STANDALONE),
            ("CREATE UNIQUE INDEX CONCURRENTLY i ON t (a)", StatementKind.STANDALONE),
            ("DROP INDEX CONCURRENTLY i", StatementKind.STANDALONE),
            ("REINDEX TABLE CONCURRENTLY t", StatementKind.STANDALONE),
            ("REINDEX DATABASE app", StatementKind.STANDALONE),
            ("REINDEX (VERBOSE) SYSTEM app", StatementKind.STANDALONE),
            ("REINDEX TABLE t", StatementKind.WRITE),
            ("ALTER SYSTEM SET work_mem = '64MB'", StatementKind.STANDALONE),
            ("ALTER TABLE t ADD COLUMN a int", StatementKind.WRITE),
            ("CLUSTER", StatementKind.STANDALONE),
            ("CLUSTER VERBOSE", StatementKind.STANDALONE),
            ("CLUSTER t USING i", StatementKind.WRITE),
            ("DISCARD ALL", StatementKind.STANDALONE),
        ],
    )
    def test_kind(self, sql: object, kind: StatementKind) -> None:
        assert statement_kind(sql) is kind


class TestCalls:
    """A read that calls a function no read takes counts as a write."""

    @pytest.mark.parametrize(
        "sql",
        [
            'SELECT COUNT(*) AS "__count" FROM "auth_group"',
            'SELECT "a"."id" FROM "a" WHERE "a"."id" IN (SELECT U0."id" FROM "b" U0)',
            'SELECT COALESCE(SUM("x"), 0), LOWER("name") FROM t',
            'SELECT ("a"."x")::numeric(10, 2) FROM "a"',
            "SELECT django_date_trunc('day', \"d\", NULL, NULL) FROM t",
            'SELECT ROW_NUMBER() OVER (PARTITION BY "a") FROM t',
            "SELECT 1 WHERE EXISTS(SELECT 1) AND ('a', 'b') IN (VALUES ('a', 'b'))",
            'SELECT "a", ("b") FROM t',
            "SELECT 'f(' FROM t",
            "SELECT 1 /* refresh(1) */",
            "SELECT $$ refresh() $$",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ],
    )
    def test_pure_calls_read(self, sql: str) -> None:
        assert statement_kind(sql) is StatementKind.READ

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT refresh_totals()",
            "SELECT ñpurge()",
            "SELECT 1purge()",
            "SELECT ñ()",
            "SELECT $purge()",
            "SELECT nextval('seq')",
            "SELECT pg_advisory_lock(1)",
            "SELECT app.archive(%s)",
            'SELECT "Archive"(%s)',
            "SELECT * FROM t WHERE id = set_flag (1)",
            "SELECT refresh /* note */ (1)",
            "WITH x AS (SELECT bump()) SELECT * FROM x",
            "SELECT 'unclosed refresh(",
            "SELECT myschema.lower(x) FROM t",
            "SELECT myschema . /* x */ lower (x) FROM t",
            'SELECT myschema."lower"(x) FROM t',
            "SELECT myschema.lower(x) FROM t FOR UPDATE",
        ],
    )
    def test_an_unknown_call_writes(self, sql: str) -> None:
        assert statement_kind(sql) is StatementKind.WRITE


class TestAmbiguousText:
    """A statement the lexer cannot read as one sure read counts as a write."""

    @pytest.mark.parametrize(
        ("sql", "kind"),
        [
            ("SELECT 1; UPDATE t SET a = 1", StatementKind.WRITE),
            ("SELECT 1; DROP TABLE t", StatementKind.WRITE),
            ("SELECT 1 ;\n-- note\n TRUNCATE t", StatementKind.WRITE),
            ("SET search_path = a; DELETE FROM t", StatementKind.WRITE),
            ('SAVEPOINT "s1"; DELETE FROM t', StatementKind.WRITE),
            ("COMMIT; DROP TABLE t", StatementKind.WRITE),
            ("VACUUM; DROP TABLE t", StatementKind.WRITE),
            ("BEGIN; DELETE FROM t", StatementKind.WRITE),
            ("DISCARD ALL; DROP TABLE t", StatementKind.WRITE),
            ("SELECT 1;", StatementKind.READ),
            ("SELECT 1; -- done", StatementKind.READ),
            ("SELECT 'a;b' FROM t", StatementKind.READ),
            ("SET search_path = 'a;b'", StatementKind.SESSION),
            ("SET x = 'a;b", StatementKind.WRITE),
            ("DISCARD ALL; /* done */", StatementKind.STANDALONE),
            ("SELECT E'\\'' ; DELETE FROM t; --'", StatementKind.WRITE),
            ("SELECT '\\'' ; DROP TABLE t; --'", StatementKind.WRITE),
            ("SELECT '\\' ; DROP TABLE t; --'", StatementKind.WRITE),
            ("SET x = E'\\'' ; DELETE FROM t; --'", StatementKind.WRITE),
            (
                "CREATE INDEX CONCURRENTLY i ON t (a) WHERE b = '\\'",
                StatementKind.STANDALONE,
            ),
            (
                "CREATE INDEX CONCURRENTLY i ON t (a) WHERE b = E'\\'' ; DROP TABLE t --'",
                StatementKind.WRITE,
            ),
            (
                "SELECT * FROM t WHERE a LIKE %s ESCAPE '\\' AND b IN (SELECT 1)",
                StatementKind.READ,
            ),
            ("SELECT COUNT(*) FROM t WHERE a LIKE %s ESCAPE '\\'", StatementKind.READ),
            ("SELECT 'C:\\dir' FROM t WHERE a = 'x;y'", StatementKind.READ),
            ("EXPLAIN ANALYZE CREATE TABLE t2 AS SELECT * FROM t", StatementKind.WRITE),
            (
                "EXPLAIN (ANALYZE) CREATE MATERIALIZED VIEW mv AS SELECT 1",
                StatementKind.WRITE,
            ),
            ("EXPLAIN (ANALYZE, FORMAT JSON) EXECUTE stmt", StatementKind.WRITE),
            ("EXPLAIN ANALYZE EXECUTE stmt", StatementKind.WRITE),
            ("EXPLAIN ANALYSE DELETE FROM t", StatementKind.WRITE),
            ("EXPLAIN ANALYZE 'x'", StatementKind.WRITE),
            ("EXPLAIN ANALYZE", StatementKind.WRITE),
            ("DESCRIBE ANALYZE INSERT INTO t VALUES (1)", StatementKind.WRITE),
            ("EXPLAIN ANALYZE SELECT 1", StatementKind.READ),
            ("EXPLAIN (ANALYZE true, FORMAT JSON) SELECT 1", StatementKind.READ),
            ("EXPLAIN ANALYZE FORMAT=TREE SELECT 1", StatementKind.READ),
            (
                "EXPLAIN ANALYZE VERBOSE WITH x AS (SELECT 1) TABLE x",
                StatementKind.READ,
            ),
            ("EXPLAIN QUERY PLAN SELECT 1", StatementKind.READ),
            ("EXPLAIN EXECUTE stmt", StatementKind.READ),
        ],
    )
    def test_kind(self, sql: str, kind: StatementKind) -> None:
        assert statement_kind(sql) is kind
