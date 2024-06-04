# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

# Databases (SQLAlchemy) models.

from sqlalchemy import (JSON, BigInteger, Boolean, Column, DateTime, Enum, ForeignKey,
                        Integer, String)
from sqlalchemy.orm import relationship

from .database import Base


class Run(Base):
    """This contains both indexing and search runs."""
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    run_type = Column(String)  # 'search' or 'indexing'. TODO: use StrEnum.
    track = Column(String, index=True)
    engine = Column(String, index=True)
    storage = Column(String, index=True)
    instance = Column(String, index=True)
    tag = Column(String, index=True)
    commit_hash = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    unsafe_user = Column(String, index=True)
    verified_email = Column(String, index=True)
    # Actual results, can contain a JSON representation of
    # schemas.IndexingRunResults or schemas.SearchRunResults depending
    # on 'run_type'.
    run_results = Column(JSON)
    source = Column(String, index=True)
    index_uid = Column(String, index=True)
    github_pr = Column(BigInteger, index=True)
    github_workflow_user = Column(String, index=True)
    github_workflow_run_id = Column(BigInteger, index=True)

