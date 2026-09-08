import base64
import mimetypes
import re

import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError

class WhatsappNotificationTemplate(models.Model):
    _name = "whatsapp.notification.template"
    _description = "Customer-facing WhatsApp message templates"
    _rec_name = "name"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)

    name = fields.Char(
        required=True,
        help="Friendly label shown to business users, e.g. 'Order Confirmed'. Purely "
             "descriptive — renaming this has no effect on which message gets sent; "
             "that's controlled by template_key."
    )
    template_key = fields.Char(
        required=True,
        help="Internal key the code looks up, e.g. 'order_confirmed'. Don't change this "
             "without also updating the code that references it. Hidden from the normal "
             "view — enable developer mode to see or edit it."
    )
    body = fields.Text(
        required=True,
        help="Use {placeholder} syntax for variables, e.g. {order_name}, {quantity}, {amount_total}. "
             "For broadcasts, only {name} and {phone} are available per recipient — see the "
             "Broadcasts documentation."
    )
    active = fields.Boolean(default=True)

    # --- Broadcast / Meta-approved template support -------------------------------
    # These are only required when this template needs to be sent to a customer
    # OUTSIDE the 24h WhatsApp session window (e.g. a broadcast/marketing send),
    # or when submitting the template to Meta for approval.
    meta_template_name = fields.Char(
        string="Meta Template Name",
        help="Exact name of the WhatsApp message template approved in Meta Business "
             "Manager. Required to message customers outside the 24h session window "
             "(e.g. broadcasts) and to submit this template for review. Leave empty if "
             "this template is only ever used for in-window replies/notifications."
    )
    meta_template_language = fields.Char(
        string="Meta Template Language",
        default="en_US",
        help="Language code exactly as configured for the approved template in Meta "
             "Business Manager, e.g. en_US.",
    )
    meta_category = fields.Selection(
        [("marketing", "Marketing"), ("utility", "Utility"), ("authentication", "Authentication")],
        string="Meta Category", default="marketing",
        help="Meta's template category — required to submit for review. Picking the "
             "wrong category is a common cause of rejection.",
    )
    header_type = fields.Selection(
        [("none", "None"), ("text", "Text"), ("image", "Image"),
         ("video", "Video"), ("document", "Document")],
        default="none", string="Header",
        help="Type of header component on the approved Meta template.",
    )
    header_text = fields.Char(
        string="Header Text",
        help="Static header text (no variables supported here).",
    )
    header_sample_media = fields.Binary(
        string="Header Sample File",
        help="Sample image/video/document Meta reviews when this template is submitted. "
             "Uploaded to Meta automatically when you click Submit to Meta.",
    )
    header_sample_media_filename = fields.Char(string="Header Sample Filename")
    header_image_url = fields.Char(
        string="Header Link",
        help="Public HTTPS URL Meta fetches when actually SENDING this approved template "
             "(not used for the submission sample — see Header Sample File for that). "
             "Ignored when Header is Text or None.",
    )
    footer_text = fields.Char(
        string="Footer",
        help="Optional static footer text. Meta doesn't allow variables in the footer.",
    )

    variable_ids = fields.One2many(
        "whatsapp.notification.template.variable", "template_id", string="Variables",
        help="Sample values for each {placeholder} in the body, in the order Meta will "
             "see them as {{1}}, {{2}}, ... Click 'Refresh Variables' after editing the body.",
    )
    button_ids = fields.One2many(
        "whatsapp.notification.template.button", "template_id", string="Buttons",
        help="Up to 3 buttons. Static values only (no dynamic URL suffix).",
    )

    meta_template_id = fields.Char(
        string="Meta Template ID", readonly=True,
        help="ID Meta assigned to this template after submission. Used to check status.",
    )
    meta_status = fields.Selection(
        [("not_submitted", "Not Submitted"), ("pending", "Pending Review"),
         ("approved", "Approved"), ("rejected", "Rejected"),
         ("paused", "Paused"), ("disabled", "Disabled")],
        default="not_submitted", readonly=True, string="Meta Status",
    )
    meta_rejection_reason = fields.Char(string="Rejection Reason", readonly=True)
    last_status_check = fields.Datetime(string="Last Status Check", readonly=True)

    _sql_constraints = [
        ("template_key_company_unique", "unique(template_key, company_id)",
         "Each template_key must be unique per company."),
    ]

    @api.model
    def get_template_body(self, template_key):
        template = self.search([("template_key", "=", template_key), ("active", "=", True)], limit=1)
        return template.body if template else None

    @api.onchange("name")
    def _onchange_name_set_meta_template_name(self):
        for template in self:
            if template.name and not template.meta_template_name:
                template.meta_template_name = re.sub(r"[^a-z0-9_]+", "_", template.name.strip().lower())

    def action_open_test_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Send Test Message",
            "res_model": "whatsapp.notification.template.test.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_template_id": self.id},
        }

    def _get_variable_order(self):
        """Named {placeholders} in `body`, in first-appearance order.

        Meta-approved templates use positional variables ({{1}}, {{2}}, ...).
        This lets both the broadcast sender and the Meta submission map our
        named placeholders onto those positions without a separate mapping field.
        """
        self.ensure_one()
        seen = []
        for match in re.finditer(r"\{(\w+)\}", self.body or ""):
            if match.group(1) not in seen:
                seen.append(match.group(1))
        return seen

    def action_refresh_variables(self):
        """Rebuilds `variable_ids` from the current body, in Meta's {{n}} order.
        Keeps sample values for placeholders still present; drops rows for
        placeholders removed from the body."""
        for template in self:
            names = template._get_variable_order()
            existing = {v.placeholder_name: v for v in template.variable_ids}
            template.variable_ids.filtered(lambda v: v.placeholder_name not in names).unlink()
            for i, name in enumerate(names, start=1):
                if name in existing:
                    existing[name].sequence = i
                else:
                    self.env["whatsapp.notification.template.variable"].create({
                        "template_id": template.id,
                        "sequence": i,
                        "placeholder_name": name,
                    })

    def _meta_body_text(self):
        """`body` converted from our {name} syntax to Meta's positional {{1}}, {{2}}, ..."""
        self.ensure_one()
        text = self.body or ""
        for i, name in enumerate(self._get_variable_order(), start=1):
            text = text.replace("{%s}" % name, "{{%d}}" % i)
        return text

    def _upload_header_media(self, config):
        """Uploads the sample header file to Meta's Resumable Upload API and
        returns the resulting header_handle, needed as the HEADER component's
        `example.header_handle` when submitting for review."""
        self.ensure_one()
        if not self.header_sample_media:
            raise UserError("Upload a Header Sample File before submitting.")
        if not config.meta_app_id:
            raise UserError("Set the Meta App ID on the WhatsApp Configuration first (needed to upload header media).")

        file_bytes = base64.b64decode(self.header_sample_media)
        mime_type = mimetypes.guess_type(self.header_sample_media_filename or "")[0] or {
            "image": "image/jpeg", "video": "video/mp4", "document": "application/pdf",
        }.get(self.header_type, "application/octet-stream")

        version = config.graph_api_version or "v23.0"
        try:
            session_response = requests.post(
                f"https://graph.facebook.com/{version}/{config.meta_app_id}/uploads",
                params={
                    "file_length": len(file_bytes),
                    "file_type": mime_type,
                    "access_token": config.whatsapp_token,
                },
                timeout=30,
            )
        except requests.RequestException as e:
            raise UserError(f"Could not start the Meta upload session: {e}")

        session_data = session_response.json()
        if session_response.status_code >= 400 or "id" not in session_data:
            raise UserError(
                f"Meta upload session failed ({session_response.status_code}): "
                f"{session_data.get('error', {}).get('message', session_response.text)}"
            )

        try:
            upload_response = requests.post(
                f"https://graph.facebook.com/{version}/{session_data['id']}",
                headers={"Authorization": f"OAuth {config.whatsapp_token}", "file_offset": "0"},
                data=file_bytes,
                timeout=60,
            )
        except requests.RequestException as e:
            raise UserError(f"Could not upload the header sample to Meta: {e}")

        upload_data = upload_response.json()
        if upload_response.status_code >= 400 or "h" not in upload_data:
            raise UserError(
                f"Meta file upload failed ({upload_response.status_code}): "
                f"{upload_data.get('error', {}).get('message', upload_response.text)}"
            )
        return upload_data["h"]

    def _build_meta_components(self, config):
        self.ensure_one()
        components = []

        if self.header_type == "text":
            if not self.header_text:
                raise UserError("Set Header Text before submitting a Text header.")
            components.append({"type": "HEADER", "format": "TEXT", "text": self.header_text})
        elif self.header_type in ("image", "video", "document"):
            handle = self._upload_header_media(config)
            components.append({
                "type": "HEADER",
                "format": self.header_type.upper(),
                "example": {"header_handle": [handle]},
            })

        variables = self.variable_ids.sorted("sequence")
        if variables.filtered(lambda v: not v.sample_value):
            raise UserError("Every variable needs a sample value before submitting to Meta.")

        body_component = {"type": "BODY", "text": self._meta_body_text()}
        if variables:
            body_component["example"] = {"body_text": [[v.sample_value for v in variables]]}
        components.append(body_component)

        if self.footer_text:
            components.append({"type": "FOOTER", "text": self.footer_text})

        if self.button_ids:
            if len(self.button_ids) > 3:
                raise UserError("Meta allows a maximum of 3 buttons per template.")
            buttons = []
            for button in self.button_ids:
                if button.button_type == "quick_reply":
                    buttons.append({"type": "QUICK_REPLY", "text": button.text})
                elif button.button_type == "url":
                    buttons.append({"type": "URL", "text": button.text, "url": button.value})
                elif button.button_type == "phone_number":
                    buttons.append({
                        "type": "PHONE_NUMBER", "text": button.text, "phone_number": button.value,
                    })
            components.append({"type": "BUTTONS", "buttons": buttons})

        return components

    def _get_config(self):
        self.ensure_one()
        config = self.env["whatsapp.config"].search(
            [("company_id", "=", self.company_id.id), ("active", "=", True)], limit=1
        )
        if not config:
            raise UserError(f"No active WhatsApp Configuration found for company {self.company_id.name}.")
        if not config.waba_id:
            raise UserError("Set the WhatsApp Business Account ID (waba_id) on the WhatsApp Configuration first.")
        if not config.whatsapp_token:
            raise UserError("Set a WhatsApp Access Token on the WhatsApp Configuration first.")
        return config

    def action_submit_to_meta(self):
        self.ensure_one()
        if not self.meta_template_name or not self.meta_template_language or not self.meta_category:
            raise UserError("Set Meta Template Name, Language, and Category before submitting.")

        config = self._get_config()
        components = self._build_meta_components(config)

        url = f"https://graph.facebook.com/{config.graph_api_version or 'v23.0'}/{config.waba_id}/message_templates"
        headers = {
            "Authorization": f"Bearer {config.whatsapp_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "name": self.meta_template_name,
            "language": self.meta_template_language,
            "category": self.meta_category.upper(),
            "components": components,
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=20)
        except requests.RequestException as e:
            raise UserError(f"Could not reach the Meta API: {e}")

        data = response.json()
        if response.status_code >= 400:
            raise UserError(
                f"Meta rejected the submission ({response.status_code}): "
                f"{data.get('error', {}).get('message', response.text)}"
            )

        self.write({
            "meta_template_id": data.get("id"),
            "meta_status": (data.get("status") or "pending").lower(),
            "meta_rejection_reason": False,
            "last_status_check": fields.Datetime.now(),
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Submitted to Meta",
                "message": f"Template submitted, status: {self.meta_status}.",
                "type": "success",
                "sticky": False,
            },
        }

    def action_check_status(self):
        self.ensure_one()
        if not self.meta_template_id:
            raise UserError("This template hasn't been submitted to Meta yet.")

        config = self._get_config()
        url = f"https://graph.facebook.com/{config.graph_api_version or 'v23.0'}/{self.meta_template_id}"
        headers = {"Authorization": f"Bearer {config.whatsapp_token}"}
        try:
            response = requests.get(
                url, headers=headers, params={"fields": "status,rejected_reason"}, timeout=20
            )
        except requests.RequestException as e:
            raise UserError(f"Could not reach the Meta API: {e}")

        data = response.json()
        if response.status_code >= 400:
            raise UserError(
                f"Meta status check failed ({response.status_code}): "
                f"{data.get('error', {}).get('message', response.text)}"
            )

        self.write({
            "meta_status": (data.get("status") or self.meta_status).lower(),
            "meta_rejection_reason": data.get("rejected_reason") or False,
            "last_status_check": fields.Datetime.now(),
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Status updated",
                "message": f"Meta status: {self.meta_status}.",
                "type": "success",
                "sticky": False,
            },
        }