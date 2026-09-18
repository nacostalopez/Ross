import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.billing import Plan
from app.models.relational import Account, Store, StoreCredential, User
from app.rate_limit import limiter
from app.security import encrypt_secret, hash_password

# Use test database (via docker-compose test-db service)
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://test:test@localhost:5433/escal_test",
)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter's in-memory storage is process-global and TestClient
    always presents the same "IP", so without this, login/register attempts
    from earlier tests count against later ones and cause spurious 429s.
    """
    limiter.reset()
    yield


@pytest.fixture(scope="session")
def test_db_engine():
    """Create test database engine and initialize schema."""
    engine = create_engine(TEST_DATABASE_URL, echo=False)
    
    # Create all tables
    Base.metadata.create_all(bind=engine)

    # Reference data db/init/028_billing.sql seeds in real Postgres — the
    # test DB's schema comes from these models, not that SQL file, so it
    # needs seeding here instead. Same 3 plans, same feature lists.
    with sessionmaker(bind=engine)() as seed_session:
        seed_session.add_all(
            [
                Plan(id="starter", name="Starter", max_stores=1, max_orders_per_month=500, max_users=2, features=[]),
                Plan(
                    id="growth",
                    name="Growth",
                    max_stores=5,
                    max_orders_per_month=5000,
                    max_users=5,
                    features=["weekly_report", "ltv_cohorts", "cac_by_channel", "attribution_by_channel", "forecast"],
                ),
                Plan(
                    id="scale",
                    name="Scale",
                    features=[
                        "weekly_report",
                        "ltv_cohorts",
                        "cac_by_channel",
                        "attribution_by_channel",
                        "forecast",
                        "creative_performance",
                        "store_role_overrides",
                        "audit_log",
                    ],
                ),
            ]
        )
        seed_session.commit()

    yield engine
    
    # Cleanup
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def test_db_session(test_db_engine):
    """Provide a clean database session for each test.

    Route/fixture code calls session.commit() and session.rollback() as if
    it owned a real request-scoped session. Joining the outer transaction
    directly (the naive `connection.begin()` + `sessionmaker(bind=connection)`
    recipe) breaks under that: SQLAlchemy 2.0 ends the outer transaction for
    real on the first commit(), so a later rollback() (e.g. the webhook
    route's error path) can wipe out data an earlier fixture already
    "committed" in this same test. The SAVEPOINT recipe below is what
    SQLAlchemy's docs recommend specifically to keep commit/rollback inside
    tests working exactly like production, while the whole test still rolls
    back at teardown.
    """
    connection = test_db_engine.connect()
    outer_transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()


@pytest.fixture
def client(test_db_session):
    """Provide a test client with overridden database dependency."""
    def override_get_db():
        yield test_db_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()


@pytest.fixture
def test_account(test_db_session):
    """Create a test account."""
    account = Account(id=uuid4(), name="Test Account")
    test_db_session.add(account)
    test_db_session.commit()
    test_db_session.refresh(account)
    return account


@pytest.fixture
def test_user(test_db_session, test_account):
    """Create a test user (owner of test_account — every existing test
    implicitly assumes full read/write access)."""
    user = User(
        id=uuid4(),
        account_id=test_account.id,
        email="testuser@example.com",
        hashed_password=hash_password("testpassword123"),
        role="owner",
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return user


@pytest.fixture
def admin_user(test_db_session, test_account):
    """An admin-role user in the same account as test_user."""
    user = User(
        id=uuid4(),
        account_id=test_account.id,
        email="admin@example.com",
        hashed_password=hash_password("adminpassword123"),
        role="admin",
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return user


@pytest.fixture
def viewer_user(test_db_session, test_account):
    """A viewer-role user in the same account as test_user."""
    user = User(
        id=uuid4(),
        account_id=test_account.id,
        email="viewer@example.com",
        hashed_password=hash_password("viewerpassword123"),
        role="viewer",
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return user


@pytest.fixture
def test_store(test_db_session, test_account):
    """Create a test store."""
    store = Store(
        id=uuid4(),
        account_id=test_account.id,
        name="Test Store",
        platform="shopify",
        currency="USD",
        timezone="UTC",
    )
    test_db_session.add(store)
    test_db_session.commit()
    test_db_session.refresh(store)
    return store


@pytest.fixture
def test_store_credential(test_db_session, test_store):
    """Create a test store credential."""
    credential = StoreCredential(
        id=uuid4(),
        store_id=test_store.id,
        provider="shopify",
        access_token=encrypt_secret("test_access_token_123"),
        refresh_token=encrypt_secret("test_refresh_token_456"),
    )
    test_db_session.add(credential)
    test_db_session.commit()
    test_db_session.refresh(credential)
    return credential


@pytest.fixture
def other_account(test_db_session):
    """Create another account for isolation testing."""
    account = Account(id=uuid4(), name="Other Account")
    test_db_session.add(account)
    test_db_session.commit()
    test_db_session.refresh(account)
    return account


@pytest.fixture
def other_user(test_db_session, other_account):
    """Create a user in a different account (owner of other_account)."""
    user = User(
        id=uuid4(),
        account_id=other_account.id,
        email="otheruser@example.com",
        hashed_password=hash_password("otherpassword123"),
        role="owner",
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return user


@pytest.fixture
def auth_header(client, test_user):
    """Create an authentication header for test_user."""
    response = client.post(
        "/auth/login",
        json={"email": "testuser@example.com", "password": "testpassword123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_header(client, admin_user):
    """Create an authentication header for admin_user."""
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "adminpassword123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_auth_header(client, viewer_user):
    """Create an authentication header for viewer_user."""
    response = client.post(
        "/auth/login",
        json={"email": "viewer@example.com", "password": "viewerpassword123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
