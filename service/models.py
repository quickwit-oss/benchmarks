# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

# Databases (SQLAlchemy) models.

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime, Enum, JSON
from sqlalchemy.orm import relationship

from .database import Base


class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    run_type = Column(String)  # search or indexing. TODO: use StrEnum.
    track = Column(String, index=True)
    engine = Column(String, index=True)
    storage = Column(String, index=True)
    instance = Column(String, index=True)
    tag = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    unsafe_user = Column(String, index=True)
    verified_email = Column(String, index=True)
    run_results = Column(JSON)  # json for now.
    source = Column(String, index=True)

