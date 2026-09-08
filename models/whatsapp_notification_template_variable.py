from odoo import models, fields


class WhatsappNotificationTemplateVariable(models.Model):
    _name = "whatsapp.notification.template.variable"
    _description = "Sample value for one {{n}} variable in a WhatsApp notification template"
    _order = "sequence"

    template_id = fields.Many2one("whatsapp.notification.template", required=True, ondelete="cascade")
    sequence = fields.Integer(required=True, help="Position — matches Meta's {{1}}, {{2}}, ...")
    placeholder_name = fields.Char(
        required=True, readonly=True,
        help="The {name} placeholder in the body this sample value is for.",
    )
    sample_value = fields.Char(
        required=True, help="Example value Meta shows reviewers, e.g. 'John' for {name}.",
    )

    _sql_constraints = [
        ("template_placeholder_unique", "unique(template_id, placeholder_name)",
         "Each placeholder can only have one sample value per template."),
    ]