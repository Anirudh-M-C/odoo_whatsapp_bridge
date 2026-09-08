import requests
from odoo import models, fields, api
from odoo.exceptions import UserError


class WhatsappConfig(models.Model):
    _name = "whatsapp.config"
    _description = "Per-company WhatsApp/Razorpay configuration"
    _rec_name = "phone_number_id"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    phone_number_id = fields.Char(required=True, help="Meta WhatsApp phone_number_id")
    waba_id = fields.Char(
        string="WhatsApp Business Account ID",
        help="Meta WhatsApp Business Account ID (WABA ID) — required for the Message "
             "Templates API (submitting/checking templates). Different from phone_number_id. "
             "Find it in Meta Business Manager > WhatsApp Accounts.",
    )
    meta_app_id = fields.Char(
        string="Meta App ID",
        help="Facebook App ID (from developers.facebook.com), required to upload header "
             "sample media (image/video/document) when submitting a template for review.",
    )
    active = fields.Boolean(default=True)

    whatsapp_token = fields.Char(string="WhatsApp Access Token", groups="base.group_system")
    whatsapp_verify_token = fields.Char(string="Webhook Verify Token", groups="base.group_system")
    whatsapp_app_secret = fields.Char(string="Meta App Secret", groups="base.group_system")
    graph_api_version = fields.Char(default="v23.0")

    razorpay_key_id = fields.Char(string="Razorpay Key ID", groups="base.group_system")
    razorpay_key_secret = fields.Char(string="Razorpay Key Secret", groups="base.group_system")
    razorpay_webhook_secret = fields.Char(string="Razorpay Webhook Secret", groups="base.group_system")
    razorpay_journal_id = fields.Many2one("account.journal", domain=[("type", "=", "bank")])
    payment_link_expire_hours = fields.Integer(default=24)

    fastapi_base_url = fields.Char(
        string="FastAPI Base URL",
        help="Base URL of the WhatsApp AI service, e.g. https://your-ngrok-domain.ngrok-free.app "
             "or http://localhost:8000. Used by 'Test' buttons elsewhere to reach it.",
    )
    internal_shared_secret = fields.Char(
        string="Internal Shared Secret", groups="base.group_system",
        help="Must match INTERNAL_SHARED_SECRET in the FastAPI service's .env file.",
    )

    rate_limit_window_seconds = fields.Integer(default=60)
    rate_limit_max_requests = fields.Integer(default=10)

    _sql_constraints = [
        ("phone_number_id_unique", "unique(phone_number_id)",
         "Each phone_number_id must map to exactly one WhatsApp config."),
    ]

    def _to_dict(self):
        self.ensure_one()
        return {
            "phone_number_id": self.phone_number_id,
            "whatsapp_token": self.whatsapp_token,
            "whatsapp_verify_token": self.whatsapp_verify_token,
            "whatsapp_app_secret": self.whatsapp_app_secret,
            "graph_api_version": self.graph_api_version,
            "razorpay_key_id": self.razorpay_key_id,
            "razorpay_key_secret": self.razorpay_key_secret,
            "razorpay_webhook_secret": self.razorpay_webhook_secret,
            "razorpay_journal_id": self.razorpay_journal_id.id,
            "payment_link_expire_hours": self.payment_link_expire_hours,
            "rate_limit_window_seconds": self.rate_limit_window_seconds,
            "rate_limit_max_requests": self.rate_limit_max_requests,
            "fastapi_base_url": self.fastapi_base_url,
        }

    @api.model
    def get_config_by_phone_number_id(self, phone_number_id):
        config = self.search([("phone_number_id", "=", phone_number_id), ("active", "=", True)], limit=1)
        return config._to_dict() if config else {}

    def action_test_whatsapp_connection(self):
        self.ensure_one()
        if not self.whatsapp_token:
            raise UserError("Set a WhatsApp Access Token first.")
        if not self.phone_number_id:
            raise UserError("Set a phone_number_id first.")

        url = f"https://graph.facebook.com/{self.graph_api_version or 'v23.0'}/{self.phone_number_id}"
        headers = {"Authorization": f"Bearer {self.whatsapp_token}"}
        try:
            response = requests.get(
                url, headers=headers,
                params={"fields": "verified_name,display_phone_number"},
                timeout=10,
            )
        except requests.RequestException as e:
            raise UserError(f"Could not reach WhatsApp Graph API: {e}")

        if response.status_code != 200:
            raise UserError(f"WhatsApp connection failed ({response.status_code}): {response.text}")

        data = response.json()
        label = data.get("verified_name") or data.get("display_phone_number") or self.phone_number_id
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "WhatsApp connection OK",
                "message": f"Connected as {label}",
                "type": "success",
                "sticky": False,
            },
        }

    def action_test_razorpay_connection(self):
        self.ensure_one()
        if not self.razorpay_key_id or not self.razorpay_key_secret:
            raise UserError("Set both Razorpay Key ID and Key Secret first.")

        try:
            response = requests.get(
                "https://api.razorpay.com/v1/payments",
                auth=(self.razorpay_key_id, self.razorpay_key_secret),
                params={"count": 1},
                timeout=10,
            )
        except requests.RequestException as e:
            raise UserError(f"Could not reach Razorpay API: {e}")

        if response.status_code != 200:
            raise UserError(f"Razorpay connection failed ({response.status_code}): {response.text}")

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Razorpay connection OK",
                "message": "Credentials are valid.",
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def find_config_by_verify_token(self, verify_token):
        """Used only by the GET /webhook verification handshake, which Meta
        sends with no phone_number_id — we don't know which company it's for
        yet, so check the token against every active config instead of one."""
        config = self.search([("whatsapp_verify_token", "=", verify_token), ("active", "=", True)], limit=1)
        return config._to_dict() if config else {}