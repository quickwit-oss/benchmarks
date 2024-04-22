# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

import os

import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# E.g.: "sqlite:///./benchmark_service.db" or for postgres:
# "postgresql+pg8000://" (and fill the env variables below).
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL")

# Env variables only used for a Postgres connection.
INSTANCE_CONNECTION_NAME = os.environ.get("INSTANCE_CONNECTION_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME = os.environ.get("DB_NAME")


def create_engine() -> sqlalchemy.Engine:
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
        return sqlalchemy.create_engine(
            SQLALCHEMY_DATABASE_URL,
            connect_args={"check_same_thread": False})

    # See https://github.com/GoogleCloudPlatform/cloud-sql-python-connector#fastapi for help.
    connector = Connector()
    return sqlalchemy.create_engine(
        SQLALCHEMY_DATABASE_URL,
        creator=lambda: connector.connect(
            INSTANCE_CONNECTION_NAME,
            "pg8000",
            user=DB_USER,
            password=DB_PASSWORD,
            db=DB_NAME,
            ip_type=IPTypes.PUBLIC,
        )
    )

engine = create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
