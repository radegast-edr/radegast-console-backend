import ssl

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import prepare_database_url_and_connect_args


def test_sqlite_url_without_timeout():
    url = "sqlite+aiosqlite:///./test.db"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "timeout=30" in new_url
    assert connect_args == {}


def test_sqlite_url_with_existing_timeout():
    url = "sqlite+aiosqlite:///./test.db?timeout=60"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "timeout=60" in new_url
    assert "timeout=30" not in new_url
    assert connect_args == {}


def test_mysql_aiomysql_ssl_verify_cert_false():
    url = "mysql+aiomysql://root:pass@localhost:3306/db?ssl_verify_cert=false"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "ssl_verify_cert" not in new_url
    assert "ssl" in connect_args
    ctx = connect_args["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_mysql_aiomysql_ssl_verify_cert_true():
    url = "mysql+aiomysql://root:pass@localhost:3306/db?ssl_verify_cert=true"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "ssl_verify_cert" not in new_url
    assert "ssl" in connect_args
    ctx = connect_args["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_mysql_aiomysql_ssl_verify_identity_false():
    url = "mysql+aiomysql://root:pass@localhost:3306/db?ssl_verify_cert=true&ssl_verify_identity=false"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "ssl_verify_cert" not in new_url
    assert "ssl_verify_identity" not in new_url
    ctx = connect_args["ssl"]
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_mysql_aiomysql_ssl_mode_disabled():
    url = "mysql+aiomysql://root:pass@localhost:3306/db?ssl_mode=DISABLED"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "ssl_mode" not in new_url
    assert "ssl" not in connect_args


def test_mysql_aiomysql_ssl_mode_required():
    url = "mysql+aiomysql://root:pass@localhost:3306/db?ssl_mode=REQUIRED"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "ssl_mode" not in new_url
    ctx = connect_args["ssl"]
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_mysql_aiomysql_ssl_mode_verify_ca():
    url = "mysql+aiomysql://root:pass@localhost:3306/db?ssl_mode=VERIFY_CA"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "ssl_mode" not in new_url
    ctx = connect_args["ssl"]
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_mysql_aiomysql_preserves_other_params():
    url = "mysql+aiomysql://user:p%40ss@db.example.com:3306/radegast?charset=utf8mb4&ssl_verify_cert=false"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "charset=utf8mb4" in new_url
    assert "ssl_verify_cert" not in new_url
    assert "ssl" in connect_args


def test_mysql_aiomysql_json_ssl():
    url = 'mysql+aiomysql://user:pass@db.example.com:3306/radegast?ssl={"check_hostname":false}'
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    assert "ssl=" not in new_url
    ctx = connect_args["ssl"]
    assert ctx.check_hostname is False


@pytest.mark.asyncio
async def test_mysql_aiomysql_engine_connect_no_type_error():
    url = "mysql+aiomysql://root:pass@127.0.0.1:54321/db?ssl_verify_cert=false"
    new_url, connect_args = prepare_database_url_and_connect_args(url)
    engine = create_async_engine(new_url, connect_args=connect_args)
    try:
        async with engine.connect():
            pass
    except Exception as e:
        assert not isinstance(e, TypeError), f"Unexpected TypeError: {e}"
        assert "ssl_verify_cert" not in str(e)


def test_mysql_aiomysql_ssl_ca_clears_x509_strict():
    url = "mysql+aiomysql://root:pass@localhost:3306/db?ssl_verify_cert=true"
    _, connect_args = prepare_database_url_and_connect_args(url)
    ctx = connect_args["ssl"]
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)
