from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, func
from sqlmodel import Field, SQLModel

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SQLModel.metadata.naming_convention = NAMING_CONVENTION


# Factory functions that create fresh Column objects per model.
# SQLModel shares sa_column Column instances across subclasses,
# causing "Column already assigned to Table" errors. Calling these
# in each model class creates unique Column objects.
def pk_field():
    return Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )


def created_at_field():
    return Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )


def updated_at_field():
    return Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
        ),
    )


class BaseModel(SQLModel):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)
