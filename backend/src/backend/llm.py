import os
import json
import logging
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator, model_validator
import re
from backend.models import Prospect

logger = logging.getLogger(__name__)

class LLMHygieneError(Exception):
    """Exception raised when LLM generation fails Pydantic input/output hygiene standards."""
    pass

class OutreachMessage(BaseModel):
    subject: str = Field(default="", description="Email subject line. Ignore/leave empty for LinkedIn notes.")
    body: str = Field(..., description="Message body content.")
    skip: bool = Field(default=False, description="Set to true to skip this prospect if data is garbage or unqualified.")
    message_type: str = Field(..., description="Type of message: 'email' or 'linkedin'")

    @field_validator("body")
    @classmethod
    def validate_body_hygiene(cls, body: str) -> str:
        body_stripped = body.strip()
        if not body_stripped:
            return body_stripped

        # Rule 1: First sentence cannot start with "I".
        # Split sentences based on punctuation followed by space
        sentences = re.split(r'(?<=[.!?])\s+', body_stripped)
        if sentences:
            first_sentence = sentences[0].strip()
            # Clean leading quotes, spaces or special formatting
            first_sentence_clean = re.sub(r"^['\"\s]+", "", first_sentence)
            if first_sentence_clean:
                words = first_sentence_clean.split()
                first_word = words[0].rstrip(".,!?\"'") if words else ""
                if first_word.lower() == "i":
                    raise ValueError("Safety Violation: First sentence cannot start with 'I'.")

        # Rule 2: No sales slang (synergy, leverage, game-changer, cutting-edge, etc.)
        prohibited_slang = ["synergy", "leverage", "game-changer", "game changer", "cutting-edge", "cutting edge"]
        body_lower = body_stripped.lower()
        for slang in prohibited_slang:
            if slang in body_lower:
                raise ValueError(f"Slang Violation: Message contains forbidden corporate buzzword '{slang}'.")

        return body_stripped

    @model_validator(mode="after")
    def validate_lengths(self) -> 'OutreachMessage':
        if self.skip:
            return self

        body_len = len(self.body)
        if self.message_type == 'linkedin':
            if body_len >= 300:
                raise ValueError(f"Length Violation: LinkedIn note is {body_len} characters (limit < 300).")
        else:
            if body_len >= 500:
                raise ValueError(f"Length Violation: Email follow-up is {body_len} characters (limit < 500).")
        return self


from backend.config import settings

class LLMEngine:
    def __init__(self):
        # Prioritize cached AgentSettings config for OpenRouter, fall back to standard env vars
        self.api_key = settings.OPENROUTER_API_KEY or os.getenv("OPENAI_API_KEY")
        self.api_base = settings.OPENROUTER_BASE_URL if settings.OPENROUTER_API_KEY else os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
        self.model_name = settings.OPENAI_MODEL if settings.OPENROUTER_API_KEY else os.getenv("OPENAI_MODEL_NAME", "gpt-4o-mini")

        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        else:
            self.client = None
            logger.warning("Neither OPENROUTER_API_KEY nor OPENAI_API_KEY was found. Operating in mock-fallback mode.")

    def generate_message(self, prospect: Prospect, channel: str, prompt_template: str) -> OutreachMessage:
        """
        Generates personalized outreach content and validates it using Pydantic rules.
        """
        # Format user prompt
        formatted_prompt = prompt_template.format(
            first_name=prospect.first_name,
            last_name=prospect.last_name or "",
            email=prospect.email or "",
            linkedin_url=prospect.linkedin_url or "",
            title=prospect.title or "Professional",
            company_name_text=prospect.company_name_text or "your company",
            personalization_angle=prospect.personalization_angle or "your recent work"
        )

        system_instruction = (
            "You are an expert copywriter generating personalized outreach. "
            "You must return a structured JSON response matching the schema. "
            "CRITICAL WRITING CONSTRAINTS:\n"
            "1. The FIRST sentence of your message body cannot start with the letter 'I' or the word 'I'. No exceptions.\n"
            "2. DO NOT use sales jargon or corporate buzzwords (synergy, leverage, game-changer, game changer, cutting-edge).\n"
            f"3. For linkedin messages, the body must be strictly under 300 characters.\n"
            f"4. For email messages, the body must be strictly under 500 characters.\n"
        )

        try:
            if not self.client:
                # Fallback mock generation for stress test and verification
                return self._generate_mock(prospect, channel, prompt_template)

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": formatted_prompt}
                ],
                response_format={"type": "json_object"}
            )
            raw_content = response.choices[0].message.content
            raw_json = json.loads(raw_content)
            
            # Make sure message_type aligns with requested channel
            raw_json['message_type'] = channel

            # Parse and validate with Pydantic
            validated_message = OutreachMessage(**raw_json)
            return validated_message

        except Exception as e:
            # Catch parsing / validation or API failure and wrap in LLMHygieneError
            raise LLMHygieneError(f"LLM Hygiene/Validation Failure: {str(e)}")

    def _generate_mock(self, prospect: Prospect, channel: str, prompt_template: str) -> OutreachMessage:
        """
        A local fallback mock generator that obeys all hygiene rules or violates them for tests.
        """
        # Create a compliant mock body
        first_name = prospect.first_name
        company = prospect.company_name_text or "your company"
        angle = prospect.personalization_angle or "your growth"
        
        # Check if the prompt_template contains specific test tags to trigger validation failures
        if "TRIGGER_I_FAIL" in prompt_template:
            body = f"I am writing because I saw your work at {company}. We should sync."
        elif "TRIGGER_SLANG_FAIL" in prompt_template:
            body = f"Hello {first_name}, let's build synergy to leverage our cutting-edge tech."
        elif "TRIGGER_LENGTH_FAIL" in prompt_template:
            # Build huge string to trigger length failure
            body = f"Hello {first_name}, " + ("a " * 350)
        else:
            # Fully compliant mock
            if channel == "email":
                body = f"Hello {first_name},\n\nHope this finds you well. Saw your personalized angle about {angle} at {company} and would love to connect to share some insights."
            else:
                body = f"Hi {first_name}, saw your work at {company} regarding {angle}. Let's connect!"

        subject = "Quick question" if channel == "email" else ""
        
        # Parse through OutreachMessage Pydantic model directly to trigger validator errors if bad mock
        validated = OutreachMessage(
            subject=subject,
            body=body,
            skip=False,
            message_type=channel
        )
        return validated
