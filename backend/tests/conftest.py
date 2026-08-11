import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, enable_sqlite_foreign_keys
from app import models  # noqa: F401
from app.models import Model


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False})
    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def model_a(db):
    model = Model(name="模型A", model_id="model-a", type="llm")
    db.add(model)
    db.commit()
    return model


@pytest.fixture()
def model_b(db):
    model = Model(name="模型B", model_id="model-b", type="llm")
    db.add(model)
    db.commit()
    return model


@pytest.fixture()
def model_c(db):
    model = Model(name="模型C", model_id="model-c", type="llm")
    db.add(model)
    db.commit()
    return model
