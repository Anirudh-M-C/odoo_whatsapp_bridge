import base64
import re

from odoo import models, api, fields


class WhatsappBridge(models.AbstractModel):
    _name = "whatsapp.bridge"
    _description = "Methods exposed to the WhatsApp/AI FastAPI service"


    def _singularize(self, word):
        lower = word.lower()
        if len(word) <= 3 or not lower.endswith("s"):
            return word
        if lower.endswith("ies") and len(word) > 4:
            return word[:-3] + "y"          # "categories" -> "category"
        if lower.endswith(("ses", "xes", "zes", "ches", "shes")):
            return word[:-2]                # "buses" -> "bus", "boxes" -> "box"
        if lower.endswith("ss"):
            return word                     # "glass" -> "glass" (don't over-strip)
        return word[:-1]                    # "chairs" -> "chair"

    def _find_product(self, product_name):
        name = product_name.strip()
        if not name:
            return self.env["product.product"]

        base_domain = [("sale_ok", "=", True)]

        # Tier 1: exact name match (case-insensitive), ignoring wildcards
        product = self.env["product.product"].search(
            base_domain + [("name", "=ilike", name)], limit=1
        )
        if product:
            return product

        # Tier 2: exact match on the singular/plural form of the whole phrase
        # e.g. "chairs" -> "chair", "chair" -> "chair" (no change)
        singular_name = self._singularize(name)
        plural_name = name if name.lower().endswith("s") else name + "s"
        product = self.env["product.product"].search(
            base_domain + [
                "|", ("name", "=ilike", singular_name), ("name", "=ilike", plural_name),
            ],
            limit=1,
        )
        if product:
            return product

        # Tier 3: loose substring fallback, but restricted to whole-word matches
        # so short/generic queries (e.g. "bus") don't match mid-word substrings
        # like "Busin engine cylinder".
        words = name.split()
        domain = list(base_domain)
        for word in words:
            singular = self._singularize(word)
            domain.append("|")
            domain.append(("name", "ilike", word))
            domain.append(("name", "ilike", singular))
        candidates = self.env["product.product"].search(domain, order="name asc", limit=50)

        patterns = [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in words]
        singular_patterns = [
            re.compile(r"\b" + re.escape(self._singularize(w)) + r"\b", re.IGNORECASE)
            for w in words
        ]
        for product in candidates:
            if all(
                p.search(product.name) or sp.search(product.name)
                for p, sp in zip(patterns, singular_patterns)
            ):
                return product

        # Nothing satisfied the word-boundary check — genuinely not found,
        # don't fall back to an unrelated substring match.
        return self.env["product.product"]

    @api.model
    def search_product(self, product_name, quantity):
        product = self._find_product(product_name)

        if not product:
            return {
                "found": False,
                "product": product_name,
                "requested_quantity": quantity,
            }

        return {
            "found": True,
            "product_id": product.id,
            "product_name": product.name,
            "requested_quantity": quantity,
            "available_quantity": product.qty_available,
            "can_fulfill": product.qty_available >= quantity,
            "currency": product.currency_id.name,
        }

    @api.model
    def get_product_details(self, product_name):
        product = self._find_product(product_name)

        if not product:
            return {"found": False, "product": product_name}

        return {
            "found": True,
            "product_id": product.id,
            "product_name": product.name,
            "price": product.list_price,
            "currency": product.currency_id.name,
            "description": product.description_sale or "",
            "qty_available": product.qty_available,
            "qty_forecasted": product.virtual_available,
            "has_image": bool(product.image_1024),
            "image_base64": product.image_1024.decode() if product.image_1024 else None,
        }
    @api.model
    def check_stock(self, product_id):
        product = self.env["product.product"].browse(product_id)

        if not product.exists():
            return {"found": False, "product_id": product_id}

        return {
            "found": True,
            "product_id": product.id,
            "product_name": product.name,
            "qty_available": product.qty_available,
            "qty_forecasted": product.virtual_available,
        }

    @api.model
    def search_products_multi(self, query, limit=5):
        name = query.strip()
        base_domain = [("sale_ok", "=", True)]

        # Exact / singular-plural matches first, then loose substring matches,
        # de-duplicated, so "chair" doesn't get buried under "Chair Legs".
        exact_ids = self.env["product.product"].search(
            base_domain + [
                "|", ("name", "=ilike", self._singularize(name)),
                ("name", "=ilike", name if name.lower().endswith("s") else name + "s"),
            ],
            limit=limit,
        ).ids

        words = name.split()
        domain = list(base_domain)
        for word in words:
            singular = self._singularize(word)
            domain.append("|")
            domain.append(("name", "ilike", word))
            domain.append(("name", "ilike", singular))
        loose_ids = self.env["product.product"].search(domain, limit=limit, order="name asc").ids

        ordered_ids = exact_ids + [i for i in loose_ids if i not in exact_ids]
        products = self.env["product.product"].browse(ordered_ids[:limit])
        return [
            {
                "product_id": p.id,
                "product_name": p.name,
                "qty_available": p.qty_available,
                "price": p.list_price,
                "currency": p.currency_id.name,
            }
            for p in products
        ]

    @api.model
    def list_available_products(self, limit=20):
        products = self.env["product.product"].search(
            [("sale_ok", "=", True), ("qty_available", ">", 0)], limit=limit
        )
        return [
            {
                "product_id": p.id,
                "product_name": p.name,
                "qty_available": p.qty_available,
                "price": p.list_price,
                "currency": p.currency_id.name,
            }
            for p in products
        ]

    @api.model
    def get_all_product_info(self):
        products = self.env["product.product"].search([("sale_ok", "=", True)])
        return [
            {
                "name": p.name,
                "description": p.description_sale or "",
                "category": p.categ_id.name or "",
            }
            for p in products
        ]


    @api.model
    def create_quotation(self, phone_number, product_id, quantity):
        if not product_id or quantity is None or quantity <= 0:
            return {"created": False, "error": "Invalid product or quantity"}

        partner = self.env["res.partner"].search(
            [("mobile", "=", phone_number)], limit=1
        )
        if not partner:
            partner = self.env["res.partner"].create({
                "name": f"WhatsApp Customer {phone_number}",
                "mobile": phone_number,
            })

        product = self.env["product.product"].browse(product_id)
        if not product.exists():
            return {"found": False, "product_id": product_id}

        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {
                "product_id": product.id,
                "product_uom_qty": quantity,
            })],
        })

        return {
            "created": True,
            "order_id": order.id,
            "order_name": order.name,
            "product_name": product.name,
            "quantity": quantity,
            "amount_total": order.amount_total,
            "currency": order.currency_id.name,
        }

    @api.model
    def get_customer_orders(self, phone_number, limit=5):
        partner = self.env["res.partner"].search(
            [("mobile", "=", phone_number)], limit=1
        )
        if not partner:
            return {"found": False, "phone_number": phone_number}

        orders = self.env["sale.order"].search(
            [("partner_id", "=", partner.id)], limit=limit, order="create_date desc"
        )
        return {
            "found": True,
            "orders": [
                {
                    "order_name": o.name,
                    "state": o.state,
                    "amount_total": o.amount_total,
                    "currency": o.currency_id.name,
                }
                for o in orders
            ],
        }


    @api.model
    def find_customer(self, phone_number):
        partner = self.env["res.partner"].search(
            [("mobile", "=", phone_number)], limit=1
        )
        if not partner:
            return {"found": False, "phone_number": phone_number}

        return {
            "found": True,
            "customer_id": partner.id,
            "name": partner.name,
            "email": partner.email or "",
            "order_count": self.env["sale.order"].search_count(
                [("partner_id", "=", partner.id)]
            ),
        }

    @api.model
    def get_order_status(self, phone_number, order_name=None):
        partner = self.env["res.partner"].search(
            [("mobile", "=", phone_number)], limit=1
        )
        if not partner:
            return {"found": False, "phone_number": phone_number}

        domain = [("partner_id", "=", partner.id)]
        if order_name:
            domain.append(("name", "=", order_name))

        order = self.env["sale.order"].search(domain, limit=1, order="create_date desc")
        if not order:
            return {"found": False, "order_name": order_name}

        return {
            "found": True,
            "order_name": order.name,
            "state": order.state,
            "invoice_status": order.invoice_status,
            "delivery_status": order.picking_ids[:1].state if order.picking_ids else "not_started",
            "amount_total": order.amount_total,
            "currency": order.currency_id.name,
        }

    @api.model
    def mark_order_paid(self, order_id, payment_reference, amount_paid):
        order = self.env["sale.order"].browse(order_id)
        if not order.exists():
            return {"found": False, "order_id": order_id}

        if order.state not in ("sale", "done"):
            for line in order.order_line:
                if line.product_id.qty_available < line.product_uom_qty:
                    order.message_post(
                        body=(
                            f"Payment received via Razorpay (Reference: {payment_reference}) but "
                            f"could not be confirmed — insufficient stock for {line.product_id.name}. "
                            f"Needs manual review and a refund."
                        )
                    )
                    return {
                        "found": True,
                        "confirmed": False,
                        "stock_issue": True,
                        "order_name": order.name,
                        "state": order.state,
                    }

            order.message_post(
                body=(
                    f"Payment received via Razorpay. "
                    f"Reference: {payment_reference}, Amount: {amount_paid} {order.currency_id.name}."
                )
            )
            order.action_confirm()

        # --- Invoice ---
        try:
            invoices = order.invoice_ids.filtered(lambda m: m.move_type == "out_invoice")
            if not invoices:
                invoices = order._create_invoices()
            invoice = invoices[:1]
            if invoice.state == "draft":
                invoice.action_post()
        except Exception as e:
            order.message_post(
                body=(
                    f"Payment received via Razorpay (Reference: {payment_reference}) but invoicing "
                    f"failed: {e}. Please create/post the invoice manually."
                )
            )
            return {
                "found": True,
                "confirmed": True,
                "invoice_created": False,
                "order_name": order.name,
                "error": str(e),
            }

        if invoice.payment_state in ("paid", "in_payment", "reversed"):
            return {
                "found": True,
                "confirmed": True,
                "already_paid": True,
                "order_name": order.name,
                "invoice_name": invoice.name,
                "state": order.state,
            }

        # --- Payment + reconciliation ---
        journal = self.env["account.journal"].search(
            [("type", "=", "bank"), ("name", "ilike", "razorpay")], limit=1
        )
        if not journal:
            journal = self.env["account.journal"].search([("type", "=", "bank")], limit=1)

        if not journal:
            order.message_post(
                body=(
                    f"Payment received via Razorpay (Reference: {payment_reference}) and invoice "
                    f"{invoice.name} was created, but no bank journal was found to register the "
                    f"payment against — please register it manually in Accounting."
                )
            )
            return {
                "found": True,
                "confirmed": True,
                "invoice_created": True,
                "payment_registered": False,
                "order_name": order.name,
                "invoice_name": invoice.name,
            }

        payment_register = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=invoice.ids
        ).create({
            "journal_id": journal.id,
            "amount": amount_paid,
            "payment_date": fields.Date.context_today(self),
        })
        payment_register._create_payments()

        return {
            "found": True,
            "confirmed": True,
            "invoice_created": True,
            "payment_registered": True,
            "order_name": order.name,
            "invoice_name": invoice.name,
            "invoice_payment_state": invoice.payment_state,
        }

    @api.model
    def log_order_note(self, order_id, note):
        order = self.env["sale.order"].browse(order_id)
        if not order.exists():
            return {"found": False, "order_id": order_id}
        order.message_post(body=note)
        return {"found": True, "order_name": order.name}

    @api.model
    def get_invoice_pdf(self, order_id):
        order = self.env["sale.order"].browse(order_id)
        if not order.exists():
            return {"found": False}

        invoice = order.invoice_ids.filtered(
            lambda m: m.move_type == "out_invoice"
        )[:1]

        if not invoice:
            return {"found": False}

        pdf_content, _ = self.env["ir.actions.report"]._render_qweb_pdf(
            "account.account_invoices",
            invoice.ids
        )

        return {
            "found": True,
            "invoice_name": invoice.name,
            "pdf_base64": base64.b64encode(pdf_content).decode(),
        }

    @api.model
    def get_invoice_pdf_by_order(self, phone_number, order_name=None):
        partner = self.env["res.partner"].search(
            [("mobile", "=", phone_number)],
            limit=1
        )

        if not partner:
            return {"found": False, "phone_number": phone_number}

        domain = [("partner_id", "=", partner.id)]

        if order_name:
            domain.append(("name", "=", order_name))

        order = self.env["sale.order"].search(
            domain,
            limit=1,
            order="create_date desc"
        )

        if not order:
            return {"found": False, "order_name": order_name}

        invoice = order.invoice_ids.filtered(
            lambda m: m.move_type == "out_invoice"
        )[:1]

        if not invoice:
            return {
                "found": False,
                "order_name": order.name,
                "reason": "no_invoice_yet",
            }

        pdf_content, _ = self.env["ir.actions.report"]._render_qweb_pdf(
            "account.account_invoices",
            invoice.ids
        )

        return {
            "found": True,
            "order_name": order.name,
            "invoice_name": invoice.name,
            "pdf_base64": base64.b64encode(pdf_content).decode(),
        }