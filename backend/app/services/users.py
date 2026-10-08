"""One rule for what makes two email addresses the same account.

An address is stored trimmed and lowercased, and every lookup compares
lowercased, so "Ana@Gmail.com " and "ana@gmail.com" are always the same
user. Before this, a phone that capitalised the first letter could register
a second account for the same person. The database backs it up with a
unique index on lower(email) (db/init/033_users_email_case_insensitive.sql).
"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(func.lower(User.email) == normalize_email(email)).first()
