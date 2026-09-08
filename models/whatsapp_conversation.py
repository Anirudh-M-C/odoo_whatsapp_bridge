import requests

from odoo import models, fields, api
from odoo.exceptions import UserError


class WhatsappConversation(models.Model):
    _name = "whatsapp.conversation"
    _inherit = ["mail.thread"]
    _description = "One WhatsApp thread per customer, tracking AI/human handling state"
    _rec_name = "partner_phone"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    phone_number_id = fields.Char(required=True, help="Which WhatsApp business number this came in on")
    partner_phone = fields.Char(required=True, help="Customer's WhatsApp number")
    partner_id = fields.Many2one("res.partner")

    state = fields.Selection(
        [
            ("ai_handling", "AI handling"),
            ("pending_human", "Pending human"),
            ("human_handling", "Human handling"),
            ("closed", "Closed"),
        ],
        default="ai_handling", required=True, tracking=True,
    )
    assigned_user_id = fields.Many2one("res.users", tracking=True)
    handoff_reason = fields.Char()
    handoff_requested_at = fields.Datetime()

    last_message_at = fields.Datetime()
    last_message_preview = fields.Char()
    unread_count = fields.Integer(default=0)

    conversation_message_ids = fields.One2many("whatsapp.conversation.message", "conversation_id")

    _sql_constraints = [
        ("phone_unique", "unique(phone_number_id, partner_phone)",
         "Each customer has exactly one conversation thread per WhatsApp number."),
    ]

    def _find_or_create(self, phone_number_id, partner_phone):
        convo = self.search([
            ("phone_number_id", "=", phone_number_id),
            ("partner_phone", "=", partner_phone),
        ], limit=1)
        if not convo:
            convo = self.create({"phone_number_id": phone_number_id, "partner_phone": partner_phone})
        return convo

    @api.model
    def get_state_for_customer(self, phone_number_id, partner_phone):
        """Called by FastAPI on every inbound message, before deciding whether
        to run the AI at all. Creates the thread on first contact."""
        convo = self._find_or_create(phone_number_id, partner_phone)
        return {
            "conversation_id": convo.id,
            "state": convo.state,
            "assigned_user_id": convo.assigned_user_id.id,
        }

    @api.model
    def request_handoff(self, phone_number_id, partner_phone, reason):
        """Pauses the AI immediately. Idempotent — calling this again while
        already pending/human_handling does nothing, so a chatty customer
        can't spam duplicate handoff log entries."""
        convo = self._find_or_create(phone_number_id, partner_phone)
        if convo.state == "ai_handling":
            convo.write({
                "state": "pending_human",
                "handoff_reason": reason,
                "handoff_requested_at": fields.Datetime.now(),
            })
            convo.message_post(body=f"Handed off to a human: {reason}")
        return {"conversation_id": convo.id, "state": convo.state}

    @api.model
    def log_message(self, phone_number_id, partner_phone, direction, sender_type, body,
                     whatsapp_message_id=None):
        """Writes one row to the permanent transcript (whatsapp.conversation.message),
        separate from the FastAPI-side sqlite history — that one is the AI's
        trimmed working memory, this one is the untrimmed record employees see."""
        convo = self._find_or_create(phone_number_id, partner_phone)
        self.env["whatsapp.conversation.message"].create({
            "conversation_id": convo.id,
            "direction": direction,
            "sender_type": sender_type,
            "body": body,
            "whatsapp_message_id": whatsapp_message_id,
        })
        convo.write({
            "last_message_at": fields.Datetime.now(),
            "last_message_preview": (body or "")[:120],
        })
        return {"conversation_id": convo.id}

    @api.model
    def get_conversation_by_id(self, conversation_id):
        convo = self.browse(conversation_id)
        if not convo.exists():
            return None
        return {
            "conversation_id": convo.id,
            "phone_number_id": convo.phone_number_id,
            "partner_phone": convo.partner_phone,
            "state": convo.state,
        }

    @api.model
    def log_message_for_conversation(self, conversation_id, direction, sender_type, body,
                                      whatsapp_message_id=None):
        convo = self.browse(conversation_id)
        if not convo.exists():
            return None
        self.env["whatsapp.conversation.message"].create({
            "conversation_id": convo.id,
            "direction": direction,
            "sender_type": sender_type,
            "body": body,
            "whatsapp_message_id": whatsapp_message_id,
        })
        convo.write({
            "last_message_at": fields.Datetime.now(),
            "last_message_preview": (body or "")[:120],
        })
        return {"conversation_id": convo.id}

    @api.model
    def get_last_inbound_at(self, phone_number_id, partner_phone):
        """Timestamp of the customer's most recent INBOUND message, or False.

        Used by the broadcast sender to decide whether a recipient is still
        inside WhatsApp's 24h customer-service window (free-text allowed) or
        outside it (a Meta-approved template is required). Deliberately looks
        at direction='inbound' specifically rather than `last_message_at`,
        which is updated on outbound sends too and would misrepresent the
        window.
        """
        convo = self.search([
            ("phone_number_id", "=", phone_number_id),
            ("partner_phone", "=", partner_phone),
        ], limit=1)
        if not convo:
            return False
        last_inbound = self.env["whatsapp.conversation.message"].search(
            [("conversation_id", "=", convo.id), ("direction", "=", "inbound")],
            order="create_date desc", limit=1,
        )
        return last_inbound.create_date if last_inbound else False

    def action_take_over(self):
        self.ensure_one()
        self.write({"state": "human_handling", "assigned_user_id": self.env.user.id})
        self.message_post(body=f"{self.env.user.name} took over this conversation.")

    def action_resume_ai(self):
        self.ensure_one()
        self.write({"state": "ai_handling", "assigned_user_id": False, "handoff_reason": False})
        self.message_post(body="AI resumed.")

    def action_close(self):
        self.ensure_one()
        self.write({"state": "closed"})
        self.message_post(body=f"Closed by {self.env.user.name}.")

    def action_send_employee_reply(self, body):
        """Called from the Transcript widget's composer. Sends `body` to the
        customer as the employee, via the FastAPI service's existing
        /internal/conversations/{id}/reply endpoint (which handles the actual
        Graph API send and writes the whatsapp.conversation.message row —
        deliberately not duplicated here).

        Server-side guard: never trust the client to only call this while
        state == human_handling; re-check here regardless of what the UI shows.
        """
        self.ensure_one()

        body = (body or "").strip()
        if not body:
            raise UserError("Message can't be empty.")
        if self.state != "human_handling":
            raise UserError("Take over this conversation before replying.")

        config = self.env["whatsapp.config"].search(
            [("phone_number_id", "=", self.phone_number_id), ("active", "=", True)], limit=1
        )
        if not config:
            raise UserError(f"No active WhatsApp Configuration found for {self.phone_number_id}.")
        if not config.fastapi_base_url or not config.internal_shared_secret:
            raise UserError(
                "Set the FastAPI Base URL and Internal Shared Secret on the WhatsApp "
                f"Configuration for {config.phone_number_id} first."
            )

        url = f"{config.fastapi_base_url.rstrip('/')}/internal/conversations/{self.id}/reply"
        headers = {"X-Internal-Secret": config.internal_shared_secret}
        try:
            response = requests.post(
                url, headers=headers,
                json={"phone_number_id": self.phone_number_id, "body": body},
                timeout=20,
            )
        except requests.RequestException as e:
            raise UserError(f"Could not reach the WhatsApp service: {e}")

        if response.status_code != 200:
            raise UserError(f"WhatsApp service returned {response.status_code}: {response.text}")

        data = response.json()
        status = data.get("status")
        if status != "ok":
            error_by_status = {
                "unauthorized": "Internal shared secret mismatch between Odoo and the WhatsApp service.",
                "lookup_failed": "Could not look up this conversation in the WhatsApp service.",
                "not_found": "This conversation was not found by the WhatsApp service.",
                "send_failed": "Sending the message via WhatsApp failed — check the WhatsApp service logs.",
            }
            raise UserError(error_by_status.get(status, f"Reply failed: {status}"))

        return True