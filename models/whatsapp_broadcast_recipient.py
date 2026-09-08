from odoo import models, fields, api


class WhatsappBroadcastRecipient(models.Model):
    _name = "whatsapp.broadcast.recipient"
    _description = "One outbound WhatsApp broadcast send, per recipient"
    _order = "id"

    campaign_id = fields.Many2one("whatsapp.broadcast.campaign", required=True, ondelete="cascade")
    partner_id = fields.Many2one("res.partner", help="Empty for manually-entered numbers with no matching customer")
    phone = fields.Char(required=True)
    display_name = fields.Char()

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id:
            self.phone = self.partner_id.mobile or self.partner_id.phone or self.phone
            self.display_name = self.partner_id.name

    state = fields.Selection(
        [("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")],
        default="pending", required=True,
    )
    send_mode = fields.Selection(
        [("text", "Free Text"), ("template", "Meta Template")],
        help="Which path was used at send time: free text (recipient was inside the 24h "
             "session window) or the Meta-approved template (outside the window).",
    )
    rendered_body = fields.Text(help="The message body as actually rendered for this recipient.")
    error_message = fields.Text()
    sent_at = fields.Datetime()
    whatsapp_message_id = fields.Char()

    _sql_constraints = [
        ("campaign_phone_unique", "unique(campaign_id, phone)",
         "Each phone number can only appear once per campaign."),
    ]