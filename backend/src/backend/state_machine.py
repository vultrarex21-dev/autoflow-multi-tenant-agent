from datetime import datetime, timedelta, timezone
from backend.models import Prospect

VALID_STATES = {'New', 'Contacted', 'Cooldown', 'Replied', 'Converted', 'do_not_contact', 'data_error'}

# Valid transitions maps: state -> set of allowed next states
# State transitions check:
# New -> Contacted
# Contacted -> Cooldown, Replied, do_not_contact, data_error
# Cooldown -> Contacted, Replied, do_not_contact, data_error
# Replied -> Converted, do_not_contact, data_error
# Converted -> do_not_contact, data_error
# do_not_contact -> data_error
# data_error -> Contacted (only via manual override)
VALID_TRANSITIONS = {
    'New': {'Contacted', 'data_error'},
    'Contacted': {'Cooldown', 'Replied', 'do_not_contact', 'data_error'},
    'Cooldown': {'Contacted', 'Replied', 'do_not_contact', 'data_error'},
    'Replied': {'Converted', 'do_not_contact', 'data_error'},
    'Converted': {'do_not_contact', 'data_error'},  # Allow opt-out even after converted
    'do_not_contact': {'data_error'},  # Opt-out is sticky and normally locked
    'data_error': set(),  # Error requires manual investigation/override
}

def transition_prospect(prospect: Prospect, target_state: str, override: bool = False) -> Prospect:
    """
    Executes a deterministic state transition for a prospect.
    Rejects illegal transitions and raises ValueError unless override=True is provided.
    """
    if target_state not in VALID_STATES:
        raise ValueError(f"Invalid target state: {target_state}")

    current_state = prospect.state or 'New'

    if current_state == target_state:
        return prospect  # No transition needed

    # Transition validation
    allowed_states = VALID_TRANSITIONS.get(current_state, set())
    
    # "Any state -> data_error" is allowed in the FRD
    if target_state == 'data_error':
        allowed = True
    else:
        allowed = target_state in allowed_states

    if not allowed and not override:
        raise ValueError(
            f"Security Override Required: Illegal transition attempted from '{current_state}' to '{target_state}' "
            f"for prospect {prospect.first_name} {prospect.last_name}."
        )

    # Apply state change
    prospect.state = target_state
    return prospect


def calculate_cooldown_expiry(outreach_count: int, days_override: int = None) -> datetime:
    """
    Computes outreach cooldown date intervals:
    - 3 days on touchpoint 1
    - 5 days on touchpoint 2
    - 7 days on touchpoint 3+
    - Custom days_override (e.g. 30 days for relative conversational replier fallback)
    """
    now = datetime.now(timezone.utc)
    if days_override is not None:
        days = days_override
    elif outreach_count == 1:
        days = 3
    elif outreach_count == 2:
        days = 5
    else:
        days = 7
        
    return now + timedelta(days=days)


def determine_next_channel(prospect: Prospect) -> str:
    """
    Calculates the omni-channel selection path for the prospect.
    Switches channels between email and linkedin if both credentials exist.
    """
    has_email = bool(prospect.email and '@' in prospect.email)
    has_linkedin = bool(prospect.linkedin_url and 'linkedin.com' in prospect.linkedin_url.lower())

    if not has_email and not has_linkedin:
        return 'none'

    last = prospect.last_channel or 'none'

    # If first touchpoint, prioritize email, fallback to linkedin
    if last == 'none':
        if has_email:
            return 'email'
        return 'linkedin'

    # Omni-channel switching logic if both channels are active
    if has_email and has_linkedin:
        if last == 'email':
            return 'linkedin'
        elif last == 'linkedin':
            return 'email'
        else:
            return 'email'

    # If only one channel is available, stick to it
    if has_email:
        return 'email'
    if has_linkedin:
        return 'linkedin'

    return 'none'
