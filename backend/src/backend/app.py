from fastapi import FastAPI, HTTPException, Depends, Query, Header
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timezone
import uuid
import httpx
import os
import logging

from backend.database import init_db, get_db, TenantRepository, TenantAccessError
from backend.models import Tenant, Prospect, ExecutionLog
from backend.state_machine import transition_prospect, calculate_cooldown_expiry, determine_next_channel
from backend.llm import LLMEngine, LLMHygieneError
from backend.config import settings

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("autoflow_app")

app = FastAPI(
    title="Autoflow Core Engine API",
    description="Deterministic multi-tenant outreach orchestrator and state engine.",
    version="1.1"
)

# Global registry tracking webhook outbound triggers (highly valuable for automated simulator assertions)
dispatched_n8n_calls = []

# Instantiate LLM Engine
llm_engine = LLMEngine()


@app.on_event("startup")
def on_startup():
    init_db()


def get_tenant_repo(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    tenant_id: Optional[str] = Query(None),
    db = Depends(get_db)
):
    """
    Flexible dependency fetching tenant identity from headers or query parameters
    to enforce repository-level DB tenant isolation boundaries.
    """
    tid = x_tenant_id or tenant_id
    if not tid:
        raise HTTPException(
            status_code=400,
            detail="Header boundary violation: Missing 'X-Tenant-ID' header or 'tenant_id' query parameter"
        )
    try:
        return TenantRepository(db=db, tenant_id=tid)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# Schema Definitions
# ==========================================

class TenantCreate(BaseModel):
    company_name: str
    tenant_id: Optional[str] = None


class ProspectCreate(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    title: Optional[str] = None
    company_name_text: Optional[str] = None
    personalization_angle: Optional[str] = None


class ProcessNextRequest(BaseModel):
    email_template: Optional[str] = None
    linkedin_template: Optional[str] = None


class ExecutionResult(BaseModel):
    prospect_id: str
    status: str  # 'success' or 'failed'
    error_message: Optional[str] = None
    message_identifier: Optional[str] = None


class GmailPubSubNotification(BaseModel):
    email: str
    thread_id: str
    message_id: Optional[str] = None


class PhantomBusterCompletion(BaseModel):
    prospect_id: str
    status: str  # 'success' or 'failed'
    message_sent: Optional[str] = None
    error_message: Optional[str] = None


class IntentOverride(BaseModel):
    intent: str  # 'booked meeting' or 'opt-out'


# ==========================================
# Endpoints
# ==========================================

@app.post("/tenants")
def create_tenant(req: TenantCreate, db = Depends(get_db)):
    """Create and register a new Tenant account."""
    tenant_id = req.tenant_id or str(uuid.uuid4())
    repo = TenantRepository(db=db, tenant_id=tenant_id)
    
    existing = repo.get_tenant()
    if existing:
        return {
            "tenant_id": str(existing.tenant_id),
            "company_name": existing.company_name,
            "created_at": existing.created_at.isoformat(),
            "status": "already_exists"
        }
        
    tenant = repo.create_tenant(req.company_name)
    return {
        "tenant_id": str(tenant.tenant_id),
        "company_name": tenant.company_name,
        "created_at": tenant.created_at.isoformat(),
        "status": "created"
    }


@app.post("/prospects")
def ingest_prospects(
    req_list: List[ProspectCreate],
    repo: TenantRepository = Depends(get_tenant_repo)
):
    """Batch ingest or register a list of prospects under this Tenant."""
    prospects_created = []
    for p in req_list:
        prospect = repo.create_prospect(
            first_name=p.first_name,
            last_name=p.last_name,
            email=p.email,
            linkedin_url=p.linkedin_url,
            title=p.title,
            company_name_text=p.company_name_text,
            personalization_angle=p.personalization_angle
        )
        prospects_created.append({
            "prospect_id": str(prospect.prospect_id),
            "name": f"{prospect.first_name} {prospect.last_name or ''}".strip(),
            "state": prospect.state
        })
    return {"status": "success", "count": len(prospects_created), "prospects": prospects_created}


@app.get("/prospects")
def list_prospects(
    state: Optional[str] = Query(None),
    repo: TenantRepository = Depends(get_tenant_repo)
):
    """Retrieve prospects belonging to this Tenant, optionally filtered by state."""
    prospects = repo.list_prospects(state=state)
    return [
        {
            "prospect_id": str(p.prospect_id),
            "first_name": p.first_name,
            "last_name": p.last_name,
            "email": p.email,
            "linkedin_url": p.linkedin_url,
            "state": p.state,
            "outreach_count": p.outreach_count,
            "last_channel": p.last_channel,
            "next_channel": p.next_channel,
            "cooldown_until": p.cooldown_until.isoformat() if p.cooldown_until else None
        } for p in prospects
    ]


@app.post("/process-next")
def process_next_outreach(
    req: ProcessNextRequest,
    repo: TenantRepository = Depends(get_tenant_repo)
):
    """
    Scans for prospects ready for outreach, evaluates the state transitions,
    generates personalized messaging, and logs the execution.
    """
    now = datetime.now(timezone.utc)
    
    # 1. Fetch prospects who are:
    # - State: 'New'
    # - State: 'Cooldown' and cooldown_until <= now
    prospects = repo.list_prospects()
    ready_prospects = []
    for p in prospects:
        if p.state == 'New':
            ready_prospects.append(p)
        elif p.state == 'Cooldown':
            if p.cooldown_until is None or p.cooldown_until <= now:
                ready_prospects.append(p)

    results = []
    
    default_email_template = (
        "Hi {first_name},\n\nHope this finds you well. I was looking at your role as {title} at {company_name_text}. "
        "I wanted to reach out because of {personalization_angle}. Would you be open to connecting?"
    )
    default_linkedin_template = (
        "Hi {first_name}, saw your profile as {title} at {company_name_text}. "
        "Intrigued by your focus on {personalization_angle}. Let's connect!"
    )
    
    email_tmpl = req.email_template or default_email_template
    linkedin_tmpl = req.linkedin_template or default_linkedin_template

    for prospect in ready_prospects:
        # Determine next channel
        channel = determine_next_channel(prospect)
        if channel == 'none':
            prospect.state = 'data_error'
            repo.db.commit()
            results.append({
                "prospect_id": str(prospect.prospect_id),
                "name": f"{prospect.first_name} {prospect.last_name or ''}".strip(),
                "status": "error",
                "detail": "No valid outreach channel available (email and linkedin are missing or invalid)."
            })
            continue

        # Execute transition to 'Contacted' first
        try:
            # Cooldown -> Contacted or New -> Contacted
            transition_prospect(prospect, 'Contacted')
            repo.db.commit()
        except ValueError as e:
            results.append({
                "prospect_id": str(prospect.prospect_id),
                "name": f"{prospect.first_name} {prospect.last_name or ''}".strip(),
                "status": "error",
                "detail": f"State transition error: {str(e)}"
            })
            continue

        # Generate outreach body using LLM
        template = email_tmpl if channel == 'email' else linkedin_tmpl
        try:
            message = llm_engine.generate_message(prospect, channel, template)
        except LLMHygieneError as e:
            # Transition to 'data_error' on hygiene or api failure!
            logger.error(f"Hygiene Check Failed for prospect {prospect.prospect_id}: {str(e)}")
            prospect.state = 'data_error'
            repo.db.commit()
            results.append({
                "prospect_id": str(prospect.prospect_id),
                "name": f"{prospect.first_name} {prospect.last_name or ''}".strip(),
                "status": "error",
                "detail": f"LLM Generation/Hygiene Violation: {str(e)}"
            })
            continue

        if message.skip:
            logger.warning(f"LLM indicated skip for prospect {prospect.prospect_id}.")
            prospect.state = 'data_error'
            repo.db.commit()
            results.append({
                "prospect_id": str(prospect.prospect_id),
                "name": f"{prospect.first_name} {prospect.last_name or ''}".strip(),
                "status": "skipped",
                "detail": "Skipped by LLM recommendation."
            })
            continue

        # Successful generation! Save Execution Log using UUID/JSONB types
        log = repo.save_execution_log(
            prospect_id=prospect.prospect_id,
            channel_used=channel,
            prompt_template=template,
            llm_raw_response=message.dict(),
            sent_content=message.body
        )

        # Transition Contacted ➔ Cooldown (calculating next execution timestamp)
        prospect.outreach_count += 1
        prospect.last_channel = channel
        prospect.last_contacted_at = datetime.now(timezone.utc)
        
        # Next cooldown timestamp calculation (today + 3, 5, or 7 days)
        cooldown_expiry = calculate_cooldown_expiry(prospect.outreach_count)
        prospect.cooldown_until = cooldown_expiry
        
        # Transition back to Cooldown
        transition_prospect(prospect, 'Cooldown')
        
        # Determine next channel for the next round
        prospect.next_channel = determine_next_channel(prospect)
        repo.db.commit()

        # Dispatch outreach trigger to n8n Webhook
        n8n_url = settings.N8N_GMAIL_WEBHOOK_URL if channel == 'email' else settings.N8N_LINKEDIN_WEBHOOK_URL
        payload = {
            "tenant_id": str(repo.tenant_id),
            "prospect_id": str(prospect.prospect_id),
            "log_id": str(log.log_id),
            "channel": channel,
            "subject": message.subject,
            "body": message.body,
            "email": prospect.email,
            "linkedin_url": prospect.linkedin_url
        }

        # Track call in global registry for testing assertions
        dispatched_n8n_calls.append(payload)

        if n8n_url:
            try:
                httpx.post(n8n_url, json=payload, timeout=2.0)
                logger.info(f"Dispatched n8n webhook for prospect {prospect.prospect_id} via {channel}")
            except Exception as ex:
                logger.error(f"Failed to post to n8n webhook: {str(ex)}")
        else:
            logger.info(f"[MOCK DISPATCH] Dispatched payload to n8n webhook: {payload}")

        results.append({
            "prospect_id": str(prospect.prospect_id),
            "name": f"{prospect.first_name} {prospect.last_name or ''}".strip(),
            "channel_used": channel,
            "sent_content": message.body,
            "status": "success",
            "cooldown_until": cooldown_expiry.isoformat()
        })

    return {"processed_count": len(results), "results": results}


@app.post("/webhooks/n8n/execution-result")
def n8n_execution_result(
    res: ExecutionResult,
    repo: TenantRepository = Depends(get_tenant_repo)
):
    """
    Webhook triggered by n8n after sending email/linkedin message.
    Updates the log with thread identifiers, or handles deliverability errors.
    """
    prospect = repo.get_prospect(res.prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if res.status == 'failed':
        # Send/Delivery failure! Transition to data_error.
        transition_prospect(prospect, 'data_error')
        repo.db.commit()
        return {"status": "success", "detail": f"Prospect moved to data_error due to send failure: {res.error_message}"}

    # On success, update message identifier in log
    if res.message_identifier:
        logs = repo.list_execution_logs(prospect_id=prospect.prospect_id)
        if logs:
            # Grab latest log
            latest_log = sorted(logs, key=lambda l: l.executed_at, reverse=True)[0]
            latest_log.message_identifier = res.message_identifier
            repo.db.commit()

    return {"status": "success", "detail": "Outreach dispatch details saved successfully."}


@app.post("/webhooks/gmail/pubsub")
def gmail_pubsub_notification(
    notification: GmailPubSubNotification,
    repo: TenantRepository = Depends(get_tenant_repo)
):
    """
    Receives incoming Gmail Push Notifications.
    Pauses active automation sequences if a reply is matched.
    """
    prospects = repo.list_prospects()
    target_prospect = None
    
    # Try finding by sender email address
    for p in prospects:
        if p.email and p.email.lower().strip() == notification.email.lower().strip():
            target_prospect = p
            break

    # If no email direct match, query by thread_id matching in execution logs
    if not target_prospect:
        logs = repo.list_execution_logs()
        for log in logs:
            if log.message_identifier == notification.thread_id:
                target_prospect = repo.get_prospect(log.prospect_id)
                if target_prospect:
                    break

    if not target_prospect:
        raise HTTPException(status_code=404, detail="No matching active prospect found for reply notification")

    # Move target prospect to 'Replied'
    try:
        transition_prospect(target_prospect, 'Replied')
        repo.db.commit()
        logger.info(f"Prospect {target_prospect.first_name} {target_prospect.last_name or ''} paused in 'Replied' state.")
        return {"status": "success", "detail": "Prospect replied. Automated outreach sequence paused."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/webhooks/phantombuster")
def phantombuster_webhook(
    pb: PhantomBusterCompletion,
    repo: TenantRepository = Depends(get_tenant_repo)
):
    """Phantombuster browser execution callback webhook."""
    prospect = repo.get_prospect(pb.prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if pb.status == 'failed':
        transition_prospect(prospect, 'data_error')
        repo.db.commit()
        return {"status": "success", "detail": f"Prospect moved to data_error due to PhantomBuster failure: {pb.error_message}"}

    return {"status": "success", "detail": "PhantomBuster completion recorded."}


@app.post("/api/prospects/{prospect_id}/intent")
def tag_intent_override(
    prospect_id: str,
    req: IntentOverride,
    repo: TenantRepository = Depends(get_tenant_repo)
):
    """
    Human administrator intent classification tags endpoint.
    Transitions prospects to 'Converted' (booked meeting) or 'do_not_contact' (opt-out).
    """
    prospect = repo.get_prospect(prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    intent = req.intent.lower().strip()
    
    try:
        if intent == 'booked meeting':
            transition_prospect(prospect, 'Converted')
        elif intent == 'opt-out':
            transition_prospect(prospect, 'do_not_contact')
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid intent value. Specify 'booked meeting' or 'opt-out'"
            )
        
        repo.db.commit()
        return {"status": "success", "detail": f"Prospect state updated to {prospect.state}."}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
