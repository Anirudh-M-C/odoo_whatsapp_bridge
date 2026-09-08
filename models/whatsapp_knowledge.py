from odoo import models, fields, api


class WhatsappKnowledgeArticle(models.Model):
    _name = "whatsapp.knowledge.article"
    _description = "FAQ entries for the WhatsApp bot's knowledge base"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    question = fields.Char(required=True)
    answer = fields.Text(required=True)
    active = fields.Boolean(default=True)

    @api.model
    def get_all_articles(self):
        articles = self.search([("active", "=", True)])
        return [{"question": a.question, "answer": a.answer} for a in articles]


class WhatsappReturnPolicy(models.Model):
    _name = "whatsapp.return.policy"
    _description = "Return policy text, optionally per product category"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    product_category_id = fields.Many2one(
        "product.category", help="Leave empty for the default/fallback policy"
    )
    policy_text = fields.Text(required=True)
    active = fields.Boolean(default=True)

    @api.model
    def get_policy_for_category(self, category_name):
        if category_name:
            specific = self.search([
                ("product_category_id.name", "=", category_name), ("active", "=", True)
            ], limit=1)
            if specific:
                return specific.policy_text

        default = self.search([("product_category_id", "=", False), ("active", "=", True)], limit=1)
        return default.policy_text if default else "You can return any unused item within 7 days of delivery for a full refund."