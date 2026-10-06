import os
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from backend.models import Base, Tenant, Prospect, ExecutionLog

from backend.config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# If running on SQLite, enforce Foreign Key constraints explicitly on connect
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if settings.DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

def init_db():
    """Create database tables if they do not exist."""
    Base.metadata.create_all(bind=engine)

@contextmanager
def get_db():
    """Retrieve standard session manager context."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TenantAccessError(Exception):
    """Exception raised when cross-tenant data boundaries are violated."""
    pass


class TenantRepository:
    """
    Repository wrapper enforcing strict multi-tenant data isolation.
    Every query and modification must pass a tenant_id parameter context.
    """
    def __init__(self, db: Session, tenant_id):
        if not tenant_id:
            raise ValueError("tenant_id must be specified to preserve isolation boundaries")
        
        # Ensure uuid type or consistent string matching
        if isinstance(tenant_id, str):
            try:
                self.tenant_id = uuid.UUID(tenant_id)
            except ValueError:
                self.tenant_id = tenant_id
        else:
            self.tenant_id = tenant_id
            
        self.db = db

    def get_tenant(self):
        """Retrieves current tenant record."""
        return self.db.query(Tenant).filter(Tenant.tenant_id == self.tenant_id).first()

    def create_tenant(self, company_name: str) -> Tenant:
        """Register a new Tenant."""
        tenant = Tenant(tenant_id=self.tenant_id, company_name=company_name)
        self.db.add(tenant)
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def get_prospect(self, prospect_id) -> Prospect:
        """Fetch a prospect. Enforces that it must belong to this tenant."""
        if isinstance(prospect_id, str):
            prospect_id = uuid.UUID(prospect_id)
            
        prospect = self.db.query(Prospect).filter(
            Prospect.prospect_id == prospect_id,
            Prospect.tenant_id == self.tenant_id
        ).first()
        return prospect

    def list_prospects(self, state=None):
        """List prospects belonging to this tenant, optionally filtering by state."""
        query = self.db.query(Prospect).filter(Prospect.tenant_id == self.tenant_id)
        if state:
            query = query.filter(Prospect.state == state)
        return query.all()

    def create_prospect(self, first_name: str, last_name: str = None, email: str = None, linkedin_url: str = None, **kwargs) -> Prospect:
        """Create a new prospect locked to this tenant."""
        prospect = Prospect(
            tenant_id=self.tenant_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            linkedin_url=linkedin_url,
            **kwargs
        )
        self.db.add(prospect)
        self.db.commit()
        self.db.refresh(prospect)
        return prospect

    def save_execution_log(self, prospect_id, channel_used: str, prompt_template: str, llm_raw_response: dict, sent_content: str, message_identifier: str = None) -> ExecutionLog:
        """Save a new outreach execution log, validating tenant owner boundaries."""
        # Force strict lookup checking
        prospect = self.get_prospect(prospect_id)
        if not prospect:
            raise TenantAccessError(
                f"Security Boundary Exception: Prospect {prospect_id} does not belong to Tenant {self.tenant_id}"
            )

        log = ExecutionLog(
            tenant_id=self.tenant_id,
            prospect_id=prospect.prospect_id,
            channel_used=channel_used,
            prompt_template=prompt_template,
            llm_raw_response=llm_raw_response,
            sent_content=sent_content,
            message_identifier=message_identifier
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log

    def list_execution_logs(self, prospect_id=None):
        """Retrieve execution logs for this tenant, optionally filtering by a specific prospect."""
        query = self.db.query(ExecutionLog).filter(ExecutionLog.tenant_id == self.tenant_id)
        if prospect_id:
            if isinstance(prospect_id, str):
                prospect_id = uuid.UUID(prospect_id)
            query = query.filter(ExecutionLog.prospect_id == prospect_id)
        return query.all()
