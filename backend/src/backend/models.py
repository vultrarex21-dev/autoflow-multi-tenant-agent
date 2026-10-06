import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, CheckConstraint, JSON, text
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB as PG_JSONB

Base = declarative_base()

class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses CHAR(36), storing as stringified hex values.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return value
        else:
            if isinstance(value, uuid.UUID):
                return str(value)
            else:
                return value

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                return uuid.UUID(value)
            return value


class NativeJSONB(TypeDecorator):
    """Platform-independent JSONB type.
    Uses PostgreSQL's JSONB type, otherwise uses standard JSON.
    """
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_JSONB)
        else:
            return dialect.type_descriptor(JSON)


class Tenant(Base):
    __tablename__ = 'tenants'

    tenant_id = Column(GUID, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    company_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("CURRENT_TIMESTAMP"))

    prospects = relationship("Prospect", back_populates="tenant", cascade="all, delete-orphan")
    execution_logs = relationship("ExecutionLog", back_populates="tenant", cascade="all, delete-orphan")


class Prospect(Base):
    __tablename__ = 'prospects'

    prospect_id = Column(GUID, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id = Column(GUID, ForeignKey('tenants.tenant_id', ondelete='CASCADE'), nullable=False)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100))
    email = Column(String(255))
    linkedin_url = Column(String)
    state = Column(String(50), default='New', nullable=False)
    outreach_count = Column(Integer, default=0, nullable=False)
    last_contacted_at = Column(DateTime(timezone=True))
    cooldown_until = Column(DateTime(timezone=True))
    longcooldown_count = Column(Integer, default=0, nullable=False)
    last_channel = Column(String(20), default='none')
    next_channel = Column(String(20))
    title = Column(String(150))
    company_name_text = Column(String(255))
    personalization_angle = Column(String)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("CURRENT_TIMESTAMP"))

    tenant = relationship("Tenant", back_populates="prospects")
    execution_logs = relationship("ExecutionLog", back_populates="prospect", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            state.in_(['New', 'Contacted', 'Cooldown', 'Replied', 'Converted', 'do_not_contact', 'data_error']),
            name='prospect_state_check'
        ),
        CheckConstraint(
            last_channel.in_(['email', 'linkedin', 'none']),
            name='prospect_last_channel_check'
        ),
        CheckConstraint(
            next_channel.in_(['email', 'linkedin']),
            name='prospect_next_channel_check'
        ),
    )


class ExecutionLog(Base):
    __tablename__ = 'execution_logs'

    log_id = Column(GUID, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    tenant_id = Column(GUID, ForeignKey('tenants.tenant_id', ondelete='CASCADE'), nullable=False)
    prospect_id = Column(GUID, ForeignKey('prospects.prospect_id', ondelete='CASCADE'), nullable=False)
    channel_used = Column(String(20), nullable=False)
    prompt_template = Column(String, nullable=False)
    llm_raw_response = Column(NativeJSONB, nullable=False)
    sent_content = Column(String, nullable=False)
    message_identifier = Column(String(255))
    executed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("CURRENT_TIMESTAMP"))

    tenant = relationship("Tenant", back_populates="execution_logs")
    prospect = relationship("Prospect", back_populates="execution_logs")
