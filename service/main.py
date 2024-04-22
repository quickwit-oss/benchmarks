# Copyright 2024 The benchmarks Authors
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

import datetime
import logging
import os
import time
import urllib.parse
from typing import Annotated

import fastapi
import jwt
import requests
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from starlette.config import Config
from starlette.staticfiles import StaticFiles

from . import crud, models, schemas
from .database import SessionLocal, engine

models.Base.metadata.create_all(bind=engine)

logger = logging.getLogger(__name__)

app = FastAPI()

# If CORS is ever needed (e.g. if pages are not served from the
# service), see https://fastapi.tiangolo.com/tutorial/cors/
# origins = [
# ]
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=origins,
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# Observed to reduce the size of /all_runs/get_as_timeseries/
# responses by a factor ~15.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)

# Used for building the redirection URL used by Google's oauth2 flow.
DOMAIN = os.environ.get("DOMAIN", "http://localhost:9000")

# OAuth settings. They come from the Google Cloud console section
# "OAuth 2.0 Client IDs".
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
# For this to be accepted by Google's oauth2 API, it must be
# registered in Google Cloud console section "OAuth 2.0 Client IDs"
# under "Authorized redirect URIs".
GOOGLE_REDIRECT_URI = f"{DOMAIN}/auth/google"

# JWT settings.
# The secret is used for the signature in the JWT tokens. This can be
# any random string of sufficient size, e.g. `openssl rand -hex 32`.
# When the secret is changed, all previously issued JWT tokens become
# invalid, so ideally this should not change across restarts.
JWT_SECRET = os.environ.get("JWT_SECRET")
JWT_ALGO = "HS256"
JWT_EXPIRATION = datetime.timedelta(days=365)
if GOOGLE_CLIENT_ID is None or GOOGLE_CLIENT_SECRET is None or JWT_SECRET is None:
    raise Exception('Missing env variables. Expected GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, JWT_SECRET.')

ACCEPTED_SERVICE_ACCOUNT_EMAIL = "continuous-benchmark-runner@quickwit-prod.iam.gserviceaccount.com"

oauth2_scheme = fastapi.security.OAuth2AuthorizationCodeBearer(
    authorizationUrl='/login/google',
    tokenUrl='/auth/google')


@app.get("/login/google")
async def login_google(request: fastapi.Request):
    """Start Google's oauth2 flow: authenticate into Google."""
    return fastapi.responses.RedirectResponse(
        "https://accounts.google.com/o/oauth2/auth?" +
        urllib.parse.urlencode({
            "response_type": "code",
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            # We only needed minimal scope.
            "scope": "openid profile email",
        }))


def check_email(email_address: str):
    """Returns true if we accept users with the given email address."""
    return (email_address.endswith("@quickwit.io") or
            email_address == ACCEPTED_SERVICE_ACCOUNT_EMAIL)
        

def check_user_info(user_info: dict[str, str]):
    """Returns true if we accept users with the given user_info.

    Args:
      user_info: Information returned by Google's oauth2/v1/userinfo REST API.
    """
    return user_info.get("verified_email", False) and check_email(user_info.get("email", ""))


@app.get("/auth/google", response_class=HTMLResponse)
async def auth_google(code: str):
    """Authenticate into the service.

    Converts the code obtained through Google's oauth2/auth API into
    an access token, gets the authenticated email address of the user
    using that token, and if we accept it, returns a (signed) JWT
    token that allows verifying the user locally.
    See more details:
    https://developers.google.com/identity/protocols/oauth2#scenarios
    https://oauth.net/2/jwt/
    
    Args:
      code: Authentication code provided by Google's oauth2/auth API.

    Returns:
      An HTML page showing the JWT token.
    """
    token_url = "https://accounts.google.com/o/oauth2/token"
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    response = requests.post(token_url, data=data).json()
    print("ACCESS TOKEN RESPONSE:", response)
    access_token = response.get("access_token")
    user_info = requests.get("https://www.googleapis.com/oauth2/v1/userinfo", headers={"Authorization": f"Bearer {access_token}"}).json()
    if not check_user_info(user_info):
        email = user_info.get("email")
        raise HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail=f"Email '{email}' not authorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_jwt_token(user_info.get("email"))
    html = ("""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <script src="https://ajax.googleapis.com/ajax/libs/jquery/3.7.1/jquery.min.js"></script>
    <script>
    $(document).ready(function() {
      $('button').on('click', () => {
        navigator.clipboard.writeText($('textarea').val());
      });
    });
    </script>
  </head>
  <body>
    Token to pass to run.py:<br/><textarea rows="2" cols="300" readonly=1 id="token">
""" +
            token +
"""
</textarea>
<br>
<button id="button">Copy</button>
</body>
</html>
    """)
    return html


def create_jwt_token(verified_email_address: str):
    """Creates a (signed) JWT token from a verified email address."""
    now = datetime.datetime.now(datetime.timezone.utc)
    to_encode = {"exp": now + JWT_EXPIRATION,
                 "sub": verified_email_address,
                 "iat": now,
                 "iss": DOMAIN}
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGO)


def get_current_user_email(token: str = Depends(oauth2_scheme)) -> str:
    """Verify the validity of the JWT token and return the email it contains."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        email: str = payload.get('sub')
        if email is None:
            raise HTTPException(
                status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
                detail=f"Email not present in the JWT token {token}",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail=f"Expired JWT token: '{token}'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid JWT token: '{token}'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if check_email(email):
        return email

    raise HTTPException(
        status_code=fastapi.status.HTTP_401_UNAUTHORIZED,
        detail=f"Email address {email} not authorized",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/api/v1/check_jwt_token")
def check_jwt_token(email_current_user: Annotated[str, Depends(get_current_user_email)],
                    db: Session = Depends(get_db)):
    """Debug endpoint to check authentication with a JWT token."""
    return f"Authentication OK with email: {email_current_user}"


@app.post("/api/v1/indexing_runs/", response_model=schemas.IndexingRun)
def create_indexing_run(req: schemas.CreateIndexingRunRequest,
                        email_current_user: Annotated[str, Depends(get_current_user_email)],
                        db: Session = Depends(get_db)):
    """Insert an indexing run into the service."""
    if req.run.run_info.id is not None:
        raise HTTPException(status_code=400, detail="run id should not be provided")
    if req.run.run_info.timestamp is not None:
        raise HTTPException(status_code=400, detail="run timestamp should not be provided")
    if req.run.run_info.verified_email is not None:
        raise HTTPException(status_code=400, detail="verified_email should not be provided")
    if req.run.run_info.run_type is not None:
        raise HTTPException(status_code=400, detail="run type should not be provided")
    req.run.run_info.verified_email = email_current_user
    return crud.db_run_to_indexing_run(crud.create_run(db=db, run=req.run))


# TODO: consider removing.
@app.get("/api/v1/indexing_runs/{run_id}", response_model=schemas.IndexingRun)
def get_indexing_run(run_id: int, db: Session = Depends(get_db)):
    """Get an indexing run from the service."""
    try:
        run = crud.db_run_to_indexing_run(crud.get_run(db=db, run_id=run_id))
    except ValueError as e:
        raise HTTPException(status_code=404,
                            detail=f"Run with id {run_id} is not an indexing run. Details: {e}")
    return run


@app.get("/api/v1/tracks/list/")
def list_tracks(db: Session = Depends(get_db)) -> list[str]:
    """Return the list of benchmark tracks."""
    tracks = crud.list_tracks(db=db)
    return tracks


@app.get("/api/v1/all_runs/list/", response_model=schemas.ListRunsResponse)
def list_runs(run_type: str | None = None,
              track: str | None = None,
              engine: str | None = None,
              storage: str | None = None,
              instance: str | None = None,
              tag: str | None = None,
              start_timestamp: datetime.datetime | None = None,
              end_timestamp: datetime.datetime | None = None,
              unsafe_user: str | None = None,
              verified_email: str | None = None,
              source: schemas.RunSource | None = None,
              db: Session = Depends(get_db)):
    """Return the list of runs according to filters."""
    db_runs = crud.list_runs(
        db=db,
        run_type=run_type,
        track=track,
        engine=engine,
        storage=storage,
        instance=instance,
        tag=tag,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        unsafe_user=unsafe_user,
        verified_email=verified_email,
        source=source,
        ordering=crud.Ordering.DESC)
    return schemas.ListRunsResponse(
        run_infos=[crud.db_run_to_run_info(db_run)
                   for db_run in db_runs])


@app.post("/api/v1/search_runs/")
def create_search_run(req: schemas.CreateSearchRunRequest,
                      email_current_user: Annotated[str, Depends(get_current_user_email)],
                      db: Session = Depends(get_db)):
    """Insert a search run into the service."""
    if req.run.run_info.id is not None:
        raise HTTPException(status_code=400, detail="run id should not be provided")
    if req.run.run_info.timestamp is not None:
        raise HTTPException(status_code=400, detail="run timestamp should not be provided")
    if req.run.run_info.verified_email is not None:
        raise HTTPException(status_code=400, detail="verified_email should not be provided")
    if req.run.run_info.run_type is not None:
        raise HTTPException(status_code=400, detail="run type should not be provided")
    req.run.run_info.verified_email = email_current_user
    return crud.db_run_to_search_run(crud.create_run(db=db, run=req.run))


# TODO: consider removing.
@app.get("/api/v1/search_runs/{run_id}", response_model=schemas.SearchRun)
def get_search_run(run_id: int, db: Session = Depends(get_db)):
    """Get a search run from the service."""
    try:
        run = crud.db_run_to_search_run(crud.get_run(db=db, run_id=run_id))
    except ValueError as e:
        raise HTTPException(status_code=404,
                            detail=f"Run with id {run_id} is not an search run. Details: {e}")
    return run


# TODO: report error for those that are not found.
@app.post("/api/v1/all_runs/get/", response_model=schemas.GetRunsResponse)
def get_runs(req: schemas.GetRunsRequest, db: Session = Depends(get_db)):
    """Get runs from the service from a list of IDs."""
    return schemas.GetRunsResponse(
        runs=[crud.db_run_to_schema_run(db_run)
              for db_run in crud.get_runs(db=db, run_ids=req.run_ids)])


# TODO: If needed, results could be cached in-memory (and actually precomputed periodically).
# https://github.com/long2ice/fastapi-cache
@app.get("/api/v1/all_runs/get_as_timeseries/", response_model=schemas.GetRunsAsTimeseriesResponse)
def get_as_timeseries(
        track: str = "",
        engine: str = "",
        storage: str = "",
        instance: str = "",
        tag: str = "",
        start_timestamp: datetime.datetime | None = None,
        end_timestamp: datetime.datetime | None = None,
        source: schemas.RunSource | None = None,
        db: Session = Depends(get_db)):
    """Get runs from filters and format their results as timeseries."""
    db_runs = crud.list_runs(
        db=db,
        run_type=None,
        track=track,
        engine=engine,
        storage=storage,
        instance=instance,
        tag=tag,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source=source,
        return_full_runs=True,
        ordering=crud.Ordering.ASC)
    runs = [crud.db_run_to_schema_run(db_run) for db_run in db_runs]
    INDEXING_METRICS = [
        "mb_bytes_per_second",
        "indexing_duration_secs",
        "num_splits",
    ]
    SEARCH_METRICS = [
        "engine_duration",
        "total_cpu_time_s",
        "object_storage_download_megabytes",
        "object_storage_fetch_requests",
    ]
    
    # Key is (query_name or "indexing", metric_name)
    timeseries: dict[(str, str), schemas.Timeseries] = {}
    for run in runs:
        if run.run_info.run_type == "search":
            for query_result in run.run_results.queries:
                for metric_name in SEARCH_METRICS:
                    measurements = getattr(query_result, metric_name, None)
                    if measurements is None:
                        continue
                    key = (query_result.name, metric_name)
                    if key not in timeseries:
                        timeseries[key] = schemas.Timeseries(
                            name=query_result.name, metric_name=metric_name,
                            timestamps_s=[], data_points=[], tags=[], run_ids=[])
                    series = timeseries[key]
                    series.timestamps_s.append(int(run.run_info.timestamp.timestamp()))
                    series.data_points.append(measurements.median)
                    series.tags.append(
                        run.run_results.engine_info.get("build", {}).get("commit_short_hash", ""))
                    series.run_ids.append(run.run_info.id)

        if run.run_info.run_type == "indexing":
            for indexing_metric in INDEXING_METRICS:
                key = ("indexing", indexing_metric)
                if key not in timeseries:
                    timeseries[key] = schemas.Timeseries(
                        name="indexing", metric_name=indexing_metric,
                        timestamps_s=[], data_points=[], tags=[], run_ids=[])
                series = timeseries[key]
                point = getattr(run.run_results, indexing_metric)
                if point is not None:
                    series.timestamps_s.append(int(run.run_info.timestamp.timestamp()))
                    series.data_points.append(point)
                    series.tags.append(
                        run.run_results.engine_info.get("build", {}).get("commit_short_hash", ""))
                    series.run_ids.append(run.run_info.id)

    return schemas.GetRunsAsTimeseriesResponse(timeseries=timeseries.values())


# Serve ../web/build directly from the service.
# This needs to be last not to prevent access to /api
app.mount("/",
          StaticFiles(directory=os.path.join(os.path.dirname(__file__), "../web/build"), html=True),
          name="static")
