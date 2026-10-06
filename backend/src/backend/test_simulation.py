import unittest
from datetime import datetime, timezone, timedelta
import uuid

from backend.database import init_db, get_db, TenantRepository, TenantAccessError
from backend.models import Tenant, Prospect, ExecutionLog
from backend.state_machine import transition_prospect, calculate_cooldown_expiry, determine_next_channel
from backend.llm import LLMEngine, LLMHygieneError, OutreachMessage
from backend.app import app, dispatched_n8n_calls

class TestAutoflowCoreSimulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Initialize Database (SQLite by default in test mode)
        init_db()
        cls.engine = LLMEngine()

    def setUp(self):
        # Generate unique tenant IDs to isolate each test scenario
        self.tenant_1_id = uuid.uuid4()
        self.tenant_2_id = uuid.uuid4()

        with get_db() as db:
            # Register tenants
            repo_1 = TenantRepository(db, self.tenant_1_id)
            repo_2 = TenantRepository(db, self.tenant_2_id)
            
            repo_1.create_tenant("Tenant One Corp")
            repo_2.create_tenant("Tenant Two Inc")

    def test_multi_tenant_isolation(self):
        """
        Verify that Tenant A's repository can NEVER query or log actions
        against Tenant B's prospects (Strict isolation security checks).
        """
        with get_db() as db:
            repo_1 = TenantRepository(db, self.tenant_1_id)
            repo_2 = TenantRepository(db, self.tenant_2_id)

            # Tenant 1 creates a prospect
            prospect_1 = repo_1.create_prospect(
                first_name="Alice",
                last_name="Smith",
                email="alice@tenant1.com",
                linkedin_url="https://linkedin.com/in/alicesmith"
            )

            # Tenant 2 creates a prospect
            prospect_2 = repo_2.create_prospect(
                first_name="Bob",
                last_name="Jones",
                email="bob@tenant2.com",
                linkedin_url="https://linkedin.com/in/bobjones"
            )

            # Assert Tenant 1 cannot fetch Tenant 2's prospect
            fetched = repo_1.get_prospect(prospect_2.prospect_id)
            self.assertIsNone(fetched, "Security boundary failure: Tenant 1 was able to fetch Tenant 2's prospect!")

            # Assert Tenant 2 cannot fetch Tenant 1's prospect
            fetched_2 = repo_2.get_prospect(prospect_1.prospect_id)
            self.assertIsNone(fetched_2, "Security boundary failure: Tenant 2 was able to fetch Tenant 1's prospect!")

            # Assert Tenant 1 listing prospects ONLY returns Alice
            prospects_1 = repo_1.list_prospects()
            self.assertEqual(len(prospects_1), 1)
            self.assertEqual(prospects_1[0].first_name, "Alice")

            # Assert Tenant 1 logging an outreach for Bob raises TenantAccessError
            with self.assertRaises(TenantAccessError):
                repo_1.save_execution_log(
                    prospect_id=prospect_2.prospect_id,
                    channel_used="email",
                    prompt_template="Hello {first_name}",
                    llm_raw_response={"body": "Hello Bob", "message_type": "email"},
                    sent_content="Hello Bob"
                )

            print("SUCCESS: Multi-tenant isolation verified with absolute security boundary assertions.")

    def test_deterministic_state_lifecycle(self):
        """
        Verify the complete state lifecycle:
        New -> Contacted -> Cooldown -> Contacted -> Replied -> Converted
        """
        with get_db() as db:
            repo = TenantRepository(db, self.tenant_1_id)
            prospect = repo.create_prospect(
                first_name="Charlie",
                last_name="Brown",
                email="charlie@peanuts.com",
                linkedin_url="https://linkedin.com/in/charliebrown"
            )

            # Initial state is 'New'
            self.assertEqual(prospect.state, 'New')

            # 1. New -> Contacted (Touchpoint 1 sent)
            transition_prospect(prospect, 'Contacted')
            self.assertEqual(prospect.state, 'Contacted')

            # 2. Contacted -> Cooldown (with calculated expiry)
            cooldown_expiry = calculate_cooldown_expiry(outreach_count=1)
            prospect.cooldown_until = cooldown_expiry
            transition_prospect(prospect, 'Cooldown')
            self.assertEqual(prospect.state, 'Cooldown')
            self.assertGreater(prospect.cooldown_until, datetime.now(timezone.utc))

            # 3. Cooldown -> Contacted (cooldown expired, next touchpoint sent)
            transition_prospect(prospect, 'Contacted')
            self.assertEqual(prospect.state, 'Contacted')

            # 4. Contacted -> Replied (Reply detected)
            transition_prospect(prospect, 'Replied')
            self.assertEqual(prospect.state, 'Replied')

            # 5. Replied -> Converted (Intent marked booked meeting)
            transition_prospect(prospect, 'Converted')
            self.assertEqual(prospect.state, 'Converted')

            print("SUCCESS: State lifecycle transition assertions matched perfectly.")

    def test_invalid_transitions_blocked(self):
        """
        Verify that invalid state transitions are strictly rejected.
        E.g. Transitioning a 'do_not_contact' prospect back to 'Contacted' raises ValueError.
        """
        with get_db() as db:
            repo = TenantRepository(db, self.tenant_1_id)
            prospect = repo.create_prospect(
                first_name="David",
                last_name="Miller",
                email="david@miller.com",
                linkedin_url="https://linkedin.com/in/davidmiller"
            )

            # Move directly to do_not_contact
            transition_prospect(prospect, 'do_not_contact', override=True)
            self.assertEqual(prospect.state, 'do_not_contact')

            # Attempting to move back to contacted without override raises ValueError
            with self.assertRaises(ValueError):
                transition_prospect(prospect, 'Contacted')

            # Overriding transition succeeds
            transition_prospect(prospect, 'Contacted', override=True)
            self.assertEqual(prospect.state, 'Contacted')

            print("SUCCESS: Invalid transitions and manual override protection rules verified.")

    def test_llm_hygiene_validation_failures(self):
        """
        Verify Pydantic prompt boundary validators (Sales slang, first word 'I', length constraints).
        """
        with get_db() as db:
            repo = TenantRepository(db, self.tenant_1_id)
            prospect = repo.create_prospect(
                first_name="Emma",
                last_name="Watson",
                email="emma@watson.com",
                linkedin_url="https://linkedin.com/in/emmawatson"
            )

            # 1. Failure: Starts with "I"
            with self.assertRaises(LLMHygieneError):
                self.engine.generate_message(prospect, "email", "TRIGGER_I_FAIL")

            # 2. Failure: Sales slang ("synergy", "leverage", "game-changer")
            with self.assertRaises(LLMHygieneError):
                self.engine.generate_message(prospect, "email", "TRIGGER_SLANG_FAIL")

            # 3. Failure: LinkedIn character length exceedance (> 300)
            with self.assertRaises(LLMHygieneError):
                self.engine.generate_message(prospect, "linkedin", "TRIGGER_LENGTH_FAIL")

            # 4. Compliant case works cleanly
            msg = self.engine.generate_message(prospect, "email", "Standard email prompt template.")
            self.assertFalse(msg.skip)
            self.assertEqual(msg.message_type, "email")
            self.assertFalse(msg.body.lower().startswith("i "))
            self.assertNotIn("synergy", msg.body.lower())
            
            print("SUCCESS: Pydantic sales slang, sentence 'I' structure, and character length limiters enforced.")

    def test_cooldown_mathematics(self):
        """
        Verify exact delay calculation:
        - Touch 1 -> 3 days
        - Touch 2 -> 5 days
        - Touch 3+ -> 7 days
        - Override -> 30 days
        """
        now = datetime.now(timezone.utc)
        
        c1 = calculate_cooldown_expiry(1)
        self.assertAlmostEqual((c1 - now).days, 3, delta=1)

        c2 = calculate_cooldown_expiry(2)
        self.assertAlmostEqual((c2 - now).days, 5, delta=1)

        c3 = calculate_cooldown_expiry(3)
        self.assertAlmostEqual((c3 - now).days, 7, delta=1)

        c4 = calculate_cooldown_expiry(4)
        self.assertAlmostEqual((c4 - now).days, 7, delta=1)

        c_over = calculate_cooldown_expiry(1, days_override=30)
        self.assertAlmostEqual((c_over - now).days, 30, delta=1)

        print("SUCCESS: Delay mathematical formulas validated.")

    def test_channel_switching_logic(self):
        """
        Verify omni-channel switching behavior based on contact credentials.
        """
        with get_db() as db:
            repo = TenantRepository(db, self.tenant_1_id)
            
            # Case 1: Has both email and linkedin -> Switches channels
            p_both = repo.create_prospect(
                first_name="Frank",
                last_name="Green",
                email="frank@green.com",
                linkedin_url="https://linkedin.com/in/frankgreen"
            )
            # Starts with none -> email
            self.assertEqual(determine_next_channel(p_both), 'email')
            p_both.last_channel = 'email'
            # email -> linkedin
            self.assertEqual(determine_next_channel(p_both), 'linkedin')
            p_both.last_channel = 'linkedin'
            # linkedin -> email
            self.assertEqual(determine_next_channel(p_both), 'email')

            # Case 2: Only has email -> Sticks to email
            p_email = repo.create_prospect(
                first_name="Grace",
                last_name="White",
                email="grace@white.com"
            )
            self.assertEqual(determine_next_channel(p_email), 'email')
            p_email.last_channel = 'email'
            self.assertEqual(determine_next_channel(p_email), 'email')

            # Case 3: Only has linkedin -> Sticks to linkedin
            p_li = repo.create_prospect(
                first_name="Hank",
                last_name="Black",
                linkedin_url="https://linkedin.com/in/hankblack"
            )
            self.assertEqual(determine_next_channel(p_li), 'linkedin')
            p_li.last_channel = 'linkedin'
            self.assertEqual(determine_next_channel(p_li), 'linkedin')

            print("SUCCESS: Omni-channel switching and credentials fallback logic verified.")


if __name__ == "__main__":
    unittest.main()
