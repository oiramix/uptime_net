from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..models import Node
from ..main_deps import get_db

bearer = HTTPBearer(auto_error=False)


def get_current_node(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> Node:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = creds.credentials
    node = db.query(Node).filter(Node.token == token).one_or_none()
    if not node:
        raise HTTPException(status_code=401, detail="Invalid token")
    return node
