import json
import ssl
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def prepare_database_url_and_connect_args(database_url: str) -> tuple[str, dict[str, Any]]:
    """Parse and normalize the database URL and construct appropriate engine connect_args.

    - For SQLite: ensures busy_timeout is set (default to 30s) to avoid write locking issues.
    - For MySQL (aiomysql): extracts SSL-related query parameters (ssl_verify_cert,
      ssl_verify_identity, ssl_ca, ssl_cert, ssl_key, ssl_mode, ssl) and builds a valid
      ssl.SSLContext object passed via connect_args={'ssl': ctx}, while removing these parameters
      from the database URL so aiomysql.connect does not reject them with
      `TypeError: connect() got an unexpected keyword argument`.
    """
    connect_args: dict[str, Any] = {}

    if "sqlite" in database_url:
        if "timeout=" not in database_url:
            if "?" in database_url:
                database_url += "&timeout=30"
            else:
                database_url += "?timeout=30"
        return database_url, connect_args

    if "mysql" in database_url and "aiomysql" in database_url:
        url = make_url(database_url)
        query = dict(url.query)
        ssl_keys = {"ssl_verify_cert", "ssl_verify_identity", "ssl_ca", "ssl_cert", "ssl_key", "ssl_mode", "ssl"}
        found_ssl_keys = ssl_keys.intersection(query.keys())

        if found_ssl_keys:
            ssl_verify_cert = query.pop("ssl_verify_cert", None)
            ssl_verify_identity = query.pop("ssl_verify_identity", None)
            ssl_ca = query.pop("ssl_ca", None)
            ssl_cert = query.pop("ssl_cert", None)
            ssl_key = query.pop("ssl_key", None)
            ssl_mode = query.pop("ssl_mode", None)
            ssl_val = query.pop("ssl", None)

            if isinstance(ssl_val, str) and ssl_val.strip().startswith("{"):
                try:
                    ssl_dict = json.loads(ssl_val)
                    ssl_ca = ssl_ca or ssl_dict.get("ca")
                    ssl_cert = ssl_cert or ssl_dict.get("cert")
                    ssl_key = ssl_key or ssl_dict.get("key")
                    if "check_hostname" in ssl_dict:
                        ssl_verify_identity = ssl_dict["check_hostname"]
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass

            is_ssl_disabled = (
                (ssl_mode and ssl_mode.upper() == "DISABLED")
                or (isinstance(ssl_val, str) and ssl_val.lower() in ("false", "0", "no", "disabled"))
                or ssl_val is False
            )

            if not is_ssl_disabled:
                ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
                if hasattr(ssl, "VERIFY_X509_STRICT"):
                    # MySQL/MariaDB generated CA certificates often omit the X509v3 keyUsage extension.
                    # Relax strict validation to avoid "CA cert does not include key usage extension".
                    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT

                if ssl_ca:
                    ctx.load_verify_locations(cafile=ssl_ca)
                if ssl_cert:
                    ctx.load_cert_chain(certfile=ssl_cert, keyfile=ssl_key)

                if ssl_mode and ssl_mode.upper() == "REQUIRED":
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                elif ssl_mode and ssl_mode.upper() == "VERIFY_CA":
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_REQUIRED
                elif ssl_mode and ssl_mode.upper() == "VERIFY_IDENTITY":
                    ctx.check_hostname = True
                    ctx.verify_mode = ssl.CERT_REQUIRED

                is_verify_cert_false = (
                    isinstance(ssl_verify_cert, str) and ssl_verify_cert.lower() in ("false", "0", "no")
                ) or ssl_verify_cert is False
                is_verify_identity_false = (
                    isinstance(ssl_verify_identity, str) and ssl_verify_identity.lower() in ("false", "0", "no")
                ) or ssl_verify_identity is False

                if is_verify_cert_false:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                elif is_verify_identity_false:
                    ctx.check_hostname = False

                connect_args["ssl"] = ctx

            url = url.set(query=query)
            database_url = url.render_as_string(hide_password=False)

    return database_url, connect_args


db_url, connect_args = prepare_database_url_and_connect_args(settings.database_url)
settings.database_url = db_url

engine = create_async_engine(db_url, connect_args=connect_args, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, _connection_record):
    # Only run on SQLite connections
    if "sqlite" in settings.database_url:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
