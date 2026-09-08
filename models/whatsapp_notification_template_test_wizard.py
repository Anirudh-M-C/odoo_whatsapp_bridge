import re
import requests

from odoo import models, fields
from odoo.exceptions import UserError


class WhatsappNotificationTemplateTestWizard(models.TransientModel):
    _name = "whatsapp.notification.template.test.wizard"
    _description = "Send a test WhatsApp message rendered from a notification template"

    template_id = fields.Many2one("whatsapp.notification.template", required=True)
    test_phone_number = fields.Char(
        required=True,
        help="Include country code, digits only, e.g. 919876543210",
    )

    def action_send_test(self):
        self.ensure_one()
        template = self.template_id

        config = self.env["whatsapp.config"].search(
            [("company_id", "=", template.company_id.id), ("active", "=", True)], limit=1
        )
        if not config:
            raise UserError(
                f"No active WhatsApp Configuration found for company {template.company_id.name}."
            )
        if not config.whatsapp_token:
            raise UserError("That WhatsApp Configuration has no access token set.")

        # Fill every {placeholder} in the body with a dummy value so a
        # missing/renamed placeholder is caught here, not on the next real
        # order/payment event (see notify()'s KeyError in the FastAPI service).
        placeholders = set(re.findall(r"\{(\w+)\}", template.body))
        dummy_context = {name: f"[{name}]" for name in placeholders}

        try:
            message = template.body.format(**dummy_context)
        except (KeyError, IndexError, ValueError) as e:
            raise UserError(f"Template body failed to render: {e}")

        api_version = config.graph_api_version or "v23.0"
        url = f"https://graph.facebook.com/{api_version}/{config.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {config.whatsapp_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": self.test_phone_number,
            "type": "text",
            "text": {"body": f"[TEST] {message}"},
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
        except requests.RequestException as e:
            raise UserError(f"Could not reach WhatsApp Graph API: {e}")

        if response.status_code >= 400:
            raise UserError(f"WhatsApp API error ({response.status_code}): {response.text}")

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Test message sent",
                "message": f"Sent to {self.test_phone_number} using template '{template.name}'.",
                "type": "success",
                "sticky": False,
            },
        }