from odoo import models, fields, api
from odoo.exceptions import ValidationError


class WhatsappNotificationTemplateButton(models.Model):
    _name = "whatsapp.notification.template.button"
    _description = "Button on a WhatsApp notification template"
    _order = "sequence"

    template_id = fields.Many2one("whatsapp.notification.template", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    button_type = fields.Selection(
        [("quick_reply", "Quick Reply"), ("url", "URL"), ("phone_number", "Phone Number")],
        required=True, default="quick_reply",
    )
    text = fields.Char(required=True, help="Label shown to the customer (Meta limit: 25 characters).")
    value = fields.Char(
        help="URL (for URL buttons) or phone number with country code (for Phone Number buttons). "
             "Not used for Quick Reply buttons.",
    )

    @api.constrains("button_type", "value")
    def _check_value_required(self):
        for button in self:
            if button.button_type in ("url", "phone_number") and not button.value:
                raise ValidationError(f"{button.button_type.replace('_', ' ').title()} buttons need a value.")

    @api.constrains("template_id")
    def _check_max_buttons(self):
        for template in self.mapped("template_id"):
            if len(template.button_ids) > 3:
                raise ValidationError("A template can have at most 3 buttons.")