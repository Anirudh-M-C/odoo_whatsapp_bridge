import ast
import logging
from datetime import timedelta

import requests

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WhatsappBroadcastCampaign(models.Model):
    _name = "whatsapp.broadcast.campaign"
    _description = "WhatsApp broadcast/marketing campaign"
    _order = "create_date desc"

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)

    whatsapp_config_id = fields.Many2one(
        "whatsapp.config", required=True, string="Send From",
        domain="[('company_id', '=', company_id)]",
        help="Which WhatsApp business number this campaign sends from.",
    )
    template_id = fields.Many2one(
        "whatsapp.notification.template", required=True, string="Template",
        domain="[('company_id', '=', company_id)]",
    )

    # --- Audience -------------------------------------------------------------
    use_audience_filter = fields.Boolean(
        string="Use Customer Filter", default=False,
        help="Off by default. When off, the tag/date/advanced filters below are "
             "completely ignored — only 'Specific Customers' and 'Manual Numbers' "
             "are used. Turn this on only when you actually want to pull in "
             "customers by tag, order history, or a custom domain.",
    )
    partner_domain = fields.Char(
        string="Advanced Filter", default="[]",
        help="Odoo domain on Customers (res.partner), e.g. [('city','=','Mumbai')]. "
             "Combined with the quick filters below (AND). Ignored unless "
             "'Use Customer Filter' is on.",
    )
    partner_tag_ids = fields.Many2many("res.partner.category", string="Quick Filter: Tags")
    min_last_order_date = fields.Date(
        string="Quick Filter: Ordered Since",
        help="Only include customers with at least one sale order on/after this date.",
    )
    manual_numbers = fields.Text(
        string="Manual Numbers",
        help="One per line. Format: phone or phone,Name — e.g. 919876543210,Anirudh. "
             "Use this for numbers with no matching Customer record.",
    )
    partner_ids = fields.Many2many(
        "res.partner", string="Specific Customers",
        help="Hand-pick individual customers directly, independent of the filters above.",
    )

    recipient_ids = fields.One2many("whatsapp.broadcast.recipient", "campaign_id", string="Recipients")
    recipient_count = fields.Integer(compute="_compute_recipient_stats")
    pending_count = fields.Integer(compute="_compute_recipient_stats")
    sent_count = fields.Integer(compute="_compute_recipient_stats")
    failed_count = fields.Integer(compute="_compute_recipient_stats")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("recipients_generated", "Recipients Generated"),
            ("scheduled", "Scheduled"),
            ("sending", "Sending"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft", required=True, tracking=True,
    )
    scheduled_at = fields.Datetime(string="Scheduled For")
    batch_size = fields.Integer(default=50, help="Recipients sent per processing tick (rate-limit friendly).")

    @api.depends("recipient_ids.state")
    def _compute_recipient_stats(self):
        for campaign in self:
            recipients = campaign.recipient_ids
            campaign.recipient_count = len(recipients)
            campaign.pending_count = len(recipients.filtered(lambda r: r.state == "pending"))
            campaign.sent_count = len(recipients.filtered(lambda r: r.state == "sent"))
            campaign.failed_count = len(recipients.filtered(lambda r: r.state == "failed"))

    # ---------- Audience resolution ----------

    def _get_effective_partner_domain(self):
        self.ensure_one()
        try:
            domain = ast.literal_eval(self.partner_domain) if self.partner_domain else []
        except (ValueError, SyntaxError):
            raise UserError("Advanced Filter is not a valid Odoo domain, e.g. [('city','=','Mumbai')]")

        domain = list(domain)
        if self.partner_tag_ids:
            domain.append(("category_id", "in", self.partner_tag_ids.ids))
        if self.min_last_order_date:
            partner_ids_with_orders = self.env["sale.order"].search(
                [("date_order", ">=", self.min_last_order_date)]
            ).mapped("partner_id").ids
            domain.append(("id", "in", partner_ids_with_orders))
        return domain

    def action_generate_recipients(self):
        """Adds recipients matching the current filters/picks/manual numbers.

        Additive, not destructive: recipients you've hand-added or hand-removed
        in the Recipients tab are left untouched. Safe to click again after
        editing filters — it only ever adds newly-matching phone numbers, never
        removes existing rows. Use the Recipients list itself (Add a line /
        trash icon) to add or remove individual recipients directly.
        """
        for campaign in self:
            if campaign.state in ("done", "cancelled"):
                raise UserError("This campaign is already finished — create a new campaign instead.")

            existing_phones = set(campaign.recipient_ids.mapped("phone"))
            vals_list = []

            filtered_partners = self.env["res.partner"]
            if campaign.use_audience_filter:
                domain = campaign._get_effective_partner_domain()
                filtered_partners = self.env["res.partner"].search(domain)

            partners = filtered_partners | campaign.partner_ids
            for partner in partners:
                phone = (partner.mobile or partner.phone or "").strip()
                if not phone or phone in existing_phones:
                    continue
                existing_phones.add(phone)
                vals_list.append({
                    "campaign_id": campaign.id,
                    "partner_id": partner.id,
                    "phone": phone,
                    "display_name": partner.name,
                })

            for line in (campaign.manual_numbers or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split(",", 1)]
                phone = parts[0]
                display_name = parts[1] if len(parts) > 1 else ""
                if not phone or phone in existing_phones:
                    continue
                existing_phones.add(phone)
                vals_list.append({
                    "campaign_id": campaign.id,
                    "phone": phone,
                    "display_name": display_name,
                })

            if vals_list:
                self.env["whatsapp.broadcast.recipient"].create(vals_list)
            elif not campaign.recipient_ids:
                raise UserError("No recipients matched this campaign's filters, picks, or manual numbers.")

            if campaign.state == "draft":
                campaign.state = "recipients_generated"

    def action_view_recipients(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Recipients",
            "res_model": "whatsapp.broadcast.recipient",
            "view_mode": "list,form",
            "domain": [("campaign_id", "=", self.id)],
        }

    # ---------- Send / Schedule ----------

    def action_send_now(self):
        for campaign in self:
            if campaign.state != "recipients_generated":
                raise UserError("Generate recipients before sending.")
            campaign.scheduled_at = fields.Datetime.now()
            campaign.state = "sending"
            campaign._process_batch()

    def action_schedule(self):
        for campaign in self:
            if campaign.state != "recipients_generated":
                raise UserError("Generate recipients before scheduling.")
            if not campaign.scheduled_at or campaign.scheduled_at <= fields.Datetime.now():
                raise UserError("Set a future date/time to schedule this campaign.")
            campaign.state = "scheduled"

    def action_cancel(self):
        for campaign in self:
            if campaign.state == "done":
                raise UserError("A completed campaign can't be cancelled.")
            campaign.state = "cancelled"

    # ---------- Processing (called by cron) ----------

    @api.model
    def _cron_process_broadcasts(self):
        now = fields.Datetime.now()
        due = self.search([
            "|",
            ("state", "=", "sending"),
            "&", ("state", "=", "scheduled"), ("scheduled_at", "<=", now),
        ])
        for campaign in due:
            if campaign.state == "scheduled":
                campaign.state = "sending"
            try:
                campaign._process_batch()
            except Exception:
                _logger.exception("Broadcast processing failed for campaign %s", campaign.id)

    def _process_batch(self):
        """Sends up to `batch_size` still-pending recipients. Odoo decides what
        to send (window check + rendering); FastAPI's /internal/broadcast/send
        performs the actual Graph API call and reports back per-recipient
        results, which we use to update state here."""
        self.ensure_one()

        pending = self.recipient_ids.filtered(lambda r: r.state == "pending")[: self.batch_size or 50]
        if not pending:
            if not self.recipient_ids.filtered(lambda r: r.state == "pending"):
                self.state = "done"
            return

        config = self.whatsapp_config_id
        if not config.fastapi_base_url or not config.internal_shared_secret:
            raise UserError(
                "Set the FastAPI Base URL and Internal Shared Secret on the WhatsApp "
                f"Configuration for {config.phone_number_id} first."
            )

        template = self.template_id
        var_names = template._get_variable_order()
        now = fields.Datetime.now()

        payload_recipients = []
        recipient_by_id = {}

        for recipient in pending:
            variables = {
                "name": recipient.display_name or (recipient.partner_id.name or ""),
                "phone": recipient.phone,
            }
            try:
                text_body = template.body.format(**variables)
            except KeyError as e:
                recipient.write({
                    "state": "failed",
                    "error_message": f"Template references {e} which isn't available for broadcast recipients.",
                })
                continue

            last_inbound_at = self.env["whatsapp.conversation"].get_last_inbound_at(
                config.phone_number_id, recipient.phone
            )
            window_open = bool(last_inbound_at) and (now - last_inbound_at) <= timedelta(hours=24)

            if not window_open and not template.meta_template_name:
                recipient.write({
                    "state": "failed",
                    "error_message": (
                        "Recipient is outside the 24h WhatsApp session window and this "
                        "template has no approved Meta template configured."
                    ),
                })
                continue

            send_mode = "text" if window_open else "template"
            recipient_by_id[recipient.id] = recipient
            payload_recipients.append({
                "recipient_id": recipient.id,
                "phone": recipient.phone,
                "send_mode": send_mode,
                "text_body": text_body,
                "meta_template_name": template.meta_template_name,
                "meta_template_language": template.meta_template_language,
                "body_params": [variables.get(v, "") for v in var_names],
                "header_type": template.header_type,
                "header_image_url": template.header_image_url,
            })
            recipient.write({"rendered_body": text_body, "send_mode": send_mode})

        if not payload_recipients:
            return

        url = f"{config.fastapi_base_url.rstrip('/')}/internal/broadcast/send"
        headers = {"X-Internal-Secret": config.internal_shared_secret}
        try:
            response = requests.post(
                url, headers=headers,
                json={"phone_number_id": config.phone_number_id, "recipients": payload_recipients},
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            for recipient in recipient_by_id.values():
                recipient.write({"state": "failed", "error_message": f"Broadcast service unreachable: {e}"})
            return

        if data.get("status") != "ok":
            for recipient in recipient_by_id.values():
                recipient.write({"state": "failed", "error_message": data.get("error", "Unknown send error")})
            return

        Message = self.env["whatsapp.conversation.message"]
        Conversation = self.env["whatsapp.conversation"]
        for result in data.get("results", []):
            recipient = recipient_by_id.get(result.get("recipient_id"))
            if not recipient:
                continue
            if result.get("sent"):
                recipient.write({
                    "state": "sent",
                    "sent_at": fields.Datetime.now(),
                    "whatsapp_message_id": result.get("whatsapp_message_id"),
                })
                try:
                    convo = Conversation._find_or_create(config.phone_number_id, recipient.phone)
                    Message.create({
                        "conversation_id": convo.id,
                        "direction": "outbound",
                        "sender_type": "broadcast",
                        "body": f"[Broadcast: {self.name}] {recipient.rendered_body}",
                        "whatsapp_message_id": result.get("whatsapp_message_id"),
                    })
                except Exception:
                    _logger.exception("Failed to log broadcast message for recipient %s", recipient.id)
            else:
                recipient.write({"state": "failed", "error_message": result.get("error", "Send failed")})