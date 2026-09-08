import requests
from odoo import models, fields, api
from odoo.exceptions import UserError


class WhatsappAiConfig(models.Model):
    _name = "whatsapp.ai.config"
    _description = "Per-company LLM/vision/speech configuration"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)

    chat_model = fields.Char(default="openai/gpt-oss-20b")
    vision_model = fields.Char(default="qwen/qwen3.6-27b")
    speech_model = fields.Char(default="whisper-large-v3-turbo")

    system_instructions = fields.Text(required=True)

    max_tool_rounds = fields.Integer(default=4)
    max_conversation_turns = fields.Integer(default=6)

    confirm_keywords = fields.Char(default="yes,confirm,ok,okay,sure,yep,yeah,create")
    cancel_keywords = fields.Char(default="no,cancel,stop,nah")
    confirm_max_words = fields.Integer(default=3)

    handoff_keywords = fields.Char(
        default="human,agent,representative,talk to someone,talk to a person,real person,speak to someone",
        help="If the customer's message contains any of these, hand off to a human immediately."
    )

    pdf_extract_max_chars = fields.Integer(default=3000)
    rag_top_k = fields.Integer(default=2)
    rag_score_threshold = fields.Float(default=0.05)

    @api.model
    def get_config_for_company(self, phone_number_id_unused=None):
        # AI config is per-company (via company_id), not per phone_number_id —
        # the caller resolves company via the same Odoo connection already
        # scoped to that company's database.
        config = self.search([("active", "=", True)], limit=1)
        if not config:
            return {}
        return {
            "chat_model": config.chat_model,
            "vision_model": config.vision_model,
            "speech_model": config.speech_model,
            "system_instructions": config.system_instructions,
            "max_tool_rounds": config.max_tool_rounds,
            "max_conversation_turns": config.max_conversation_turns,
            "confirm_keywords": [w.strip() for w in (config.confirm_keywords or "").split(",") if w.strip()],
            "cancel_keywords": [w.strip() for w in (config.cancel_keywords or "").split(",") if w.strip()],
            "confirm_max_words": config.confirm_max_words,
            "handoff_keywords": [w.strip() for w in (config.handoff_keywords or "").split(",") if w.strip()],
            "pdf_extract_max_chars": config.pdf_extract_max_chars,
            "rag_top_k": config.rag_top_k,
            "rag_score_threshold": config.rag_score_threshold,
        }

    def action_test_ai_model(self):
        self.ensure_one()
        config = self.env["whatsapp.config"].search(
            [("company_id", "=", self.company_id.id), ("active", "=", True)], limit=1
        )
        if not config:
            raise UserError(f"No active WhatsApp Configuration found for company {self.company_id.name}.")
        if not config.fastapi_base_url or not config.internal_shared_secret:
            raise UserError(
                "Set the FastAPI Base URL and Internal Shared Secret on the "
                "WhatsApp Configuration first."
            )

        url = f"{config.fastapi_base_url.rstrip('/')}/internal/ai-config/test"
        headers = {"X-Internal-Secret": config.internal_shared_secret}
        try:
            response = requests.post(
                url, headers=headers,
                json={"phone_number_id": config.phone_number_id},
                timeout=20,
            )
        except requests.RequestException as e:
            raise UserError(f"Could not reach the AI service: {e}")

        if response.status_code != 200:
            raise UserError(f"AI service returned {response.status_code}: {response.text}")

        data = response.json()
        if data.get("status") != "ok":
            raise UserError(f"AI model test failed: {data.get('error', 'unknown error')}")

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "AI model reachable",
                "message": f"{data.get('model')} replied: {data.get('reply')}",
                "type": "success",
                "sticky": False,
            },
        }