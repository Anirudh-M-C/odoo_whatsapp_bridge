from odoo import models, fields


class WhatsappConversationMessage(models.Model):
    _name = "whatsapp.conversation.message"
    _description = "Full WhatsApp transcript per conversation, shown to employees"
    _order = "create_date asc"

    conversation_id = fields.Many2one("whatsapp.conversation", required=True, ondelete="cascade")
    direction = fields.Selection([("inbound", "Inbound"), ("outbound", "Outbound")], required=True)
    sender_type = fields.Selection(
        [
            ("customer", "Customer"),
            ("ai", "AI"),
            ("employee", "Employee"),
            ("system", "System"),
            ("broadcast", "Broadcast"),
        ],
        required=True,
    )
    body = fields.Text(required=True)
    whatsapp_message_id = fields.Char(help="Meta's message id, for future delivery-status tracking")