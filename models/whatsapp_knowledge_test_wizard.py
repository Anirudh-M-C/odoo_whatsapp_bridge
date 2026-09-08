import requests

from odoo import models, fields
from odoo.exceptions import UserError


class WhatsappKnowledgeTestWizard(models.TransientModel):
    _name = "whatsapp.knowledge.test.wizard"
    _description = "Test the WhatsApp bot's knowledge base search"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    query = fields.Char(required=True, help="A sample customer question, e.g. 'do you ship internationally'")
    result = fields.Text(readonly=True)

    def action_run_test(self):
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

        url = f"{config.fastapi_base_url.rstrip('/')}/internal/knowledge/test"
        headers = {"X-Internal-Secret": config.internal_shared_secret}
        try:
            response = requests.post(
                url, headers=headers,
                json={"phone_number_id": config.phone_number_id, "query": self.query},
                timeout=20,
            )
        except requests.RequestException as e:
            raise UserError(f"Could not reach the AI service: {e}")

        if response.status_code != 200:
            raise UserError(f"AI service returned {response.status_code}: {response.text}")

        data = response.json()
        if data.get("status") != "ok":
            raise UserError(f"Knowledge search failed: {data.get('error', 'unknown error')}")

        result = data["result"]
        if result.get("found"):
            self.result = "\n\n---\n\n".join(result["results"])
        else:
            self.result = "No matches above the configured score threshold — nothing would be returned to the customer."

        return {
            "type": "ir.actions.act_window",
            "res_model": "whatsapp.knowledge.test.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }