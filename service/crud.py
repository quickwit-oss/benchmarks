# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

import datetime
import enum

import sqlalchemy
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from . import models, schemas


# If at some point schemas.IndexingRun and schemas.SearchRun diverge,
# we'll need to split their handling and have two tables in the DB.
def create_run(db: Session, run: schemas.IndexingRun | schemas.SearchRun) -> models.Run:
    """Inserts a run (either indexing of search) into the DB and returns it."""
    fields_exclude = ['timestamp', 'id', 'run_type']
    db_run = models.Run(
        **run.run_info.model_dump(exclude=fields_exclude),
        timestamp=datetime.datetime.now(),
        run_type='indexing' if isinstance(run, schemas.IndexingRun) else 'search',
        run_results=run.run_results.model_dump(),
    )
    db.add(db_run)
    db.commit()
    db.refresh(db_run)
    return db_run


def db_run_to_run_info(db_run: models.Run | sqlalchemy.engine.row.Row) -> schemas.RunInfo:
    return schemas.RunInfo(
        id=db_run.id,
        run_type=db_run.run_type,
        timestamp=db_run.timestamp,
        track=db_run.track,
        engine=db_run.engine,
        storage=db_run.storage,
        instance=db_run.instance,
        tag=db_run.tag,
        commit_hash=db_run.commit_hash,
        unsafe_user=db_run.unsafe_user,
        verified_email=db_run.verified_email,
        source=db_run.source)


def db_run_to_schema_run(run: models.Run) -> schemas.IndexingRun | schemas.SearchRun:
    """Converts a DB run (models.Run) into a schemas.IndexingRun or SearchRun."""
    schema_cls = schemas.IndexingRun if run.run_type == 'indexing' else schemas.SearchRun
    schema_run = schema_cls(run_info=db_run_to_run_info(run),
                            run_results=run.run_results)
    return schema_run


def db_run_to_indexing_run(run: models.Run) -> schemas.IndexingRun:
    if run.run_type != 'indexing':
        raise ValueError(f'Unexpected run, got run type "{run.run_type}"')
    return schemas.IndexingRun(
        run_info=db_run_to_run_info(run),
        run_results=run.run_results)


def db_run_to_search_run(run: models.Run) -> schemas.SearchRun:
    if run.run_type != 'search':
        raise ValueError(f'Unexpected run, got type {run.run_type}')
    return schemas.SearchRun(
        run_info=db_run_to_run_info(run),
        run_results=run.run_results)


def get_run(db: Session, run_id: int) -> models.Run:
    """Fetches from the DB the run with the given ID."""
    return db.query(models.Run).filter(models.Run.id == run_id).first()


def get_runs(db: Session, run_ids: list[int]) -> list[models.Run]:
    """Fetches from the DB the runs with the given IDs."""
    return db.query(models.Run).filter(models.Run.id.in_(run_ids)).all()


class Ordering(enum.Enum):
    ARBITRARY = 1
    ASC = 2
    DESC = 3


def list_runs(db: Session,
              run_type: str | None = None,
              track: str | None = None,
              engine: str | None = None,
              storage: str | None = None,
              instance: str | None = None,
              tag: str | None = None,
              commit_hash: str | None = None,
              start_timestamp: datetime.datetime | None = None,
              end_timestamp: datetime.datetime | None = None,
              unsafe_user: str | None = None,
              verified_email: str | None = None,
              source: schemas.RunSource | None = None,
              return_full_runs: bool = False,
              ordering: Ordering = Ordering.DESC) -> list[sqlalchemy.engine.row.Row]:
    """Finds the runs with the given filters from the DB."""
    if return_full_runs:
        fields = [models.Run]
    else:
        fields = [models.Run.id,
                  models.Run.run_type,
                  models.Run.track,
                  models.Run.engine,
                  models.Run.storage,
                  models.Run.instance,
                  models.Run.tag,
                  models.Run.commit_hash,
                  models.Run.timestamp,
                  models.Run.unsafe_user,
                  models.Run.verified_email,
                  models.Run.source,
                  ]
    db_query = db.query(*fields)
    if run_type is not None:
        db_query = db_query.filter(models.Run.run_type == run_type)
    if track is not None:
        db_query = db_query.filter(models.Run.track == track)
    if engine is not None:
        db_query = db_query.filter(models.Run.engine == engine)
    if storage is not None:
        db_query = db_query.filter(models.Run.storage == storage)
    if instance is not None:
        db_query = db_query.filter(models.Run.instance == instance)
    if tag is not None:
        db_query = db_query.filter(models.Run.tag == tag)
    if commit_hash is not None:
        db_query = db_query.filter(models.Run.commit_hash == commit_hash)
    if start_timestamp is not None:
        db_query = db_query.filter(models.Run.timestamp >= start_timestamp)
    if end_timestamp is not None:
        db_query = db_query.filter(models.Run.timestamp <= end_timestamp)
    if unsafe_user is not None:
        db_query = db_query.filter(models.Run.unsafe_user == unsafe_user)
    if verified_email is not None:
        db_query = db_query.filter(models.Run.verified_email == verified_email)
    if source is not None:
        db_query = db_query.filter(models.Run.source == source)
    if ordering == Ordering.ASC:
        db_query = db_query.order_by(models.Run.timestamp.asc())
    elif ordering == Ordering.DESC:
        db_query = db_query.order_by(models.Run.timestamp.desc())
    return db_query.all()


def list_tracks(db: Session) -> list[str]:
    return [row[0] for row in db.query(models.Run.track).distinct()]

