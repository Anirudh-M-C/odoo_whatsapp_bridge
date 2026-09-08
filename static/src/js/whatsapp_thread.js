/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState, onMounted, onPatched, useRef } from "@odoo/owl";

const STATE_INFO = {
    ai_handling: { label: "AI handling", cls: "success" },
    pending_human: { label: "Pending human", cls: "warning" },
    human_handling: { label: "Human handling", cls: "info" },
    closed: { label: "Closed", cls: "secondary" },
};

/**
 * Chat-style renderer + composer for whatsapp.conversation.message
 * (the conversation_message_ids one2many field), plus a header/sidebar
 * built from other fields on the same whatsapp.conversation record.
 *
 * Visual layer only — every action still goes through the same model
 * methods that already existed before this widget:
 *   action_send_employee_reply / action_take_over / action_resume_ai / action_close
 * Nothing here talks to FastAPI or WhatsApp directly.
 */
class WhatsappThread extends Component {
    static template = "whatsapp_ai.WhatsappThread";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ draft: "", sending: false, actionPending: false });
        this.messagesRef = useRef("messages");
        this.textareaRef = useRef("textarea");

        onMounted(() => this._scrollToBottom());
        onPatched(() => this._scrollToBottom());
    }

    // ---------- scrolling ----------

    _scrollToBottom() {
        const el = this.messagesRef.el;
        if (el) {
            el.scrollTop = el.scrollHeight;
        }
    }

    // ---------- message data ----------

    get messages() {
        const list = this.props.record.data[this.props.name];
        return list ? list.records.map((r) => r.data) : [];
    }

    /**
     * Messages interleaved with day-divider entries, so long threads read
     * like a normal chat history instead of one unbroken column.
     */
    get threadItems() {
        const items = [];
        let lastDayKey = null;
        for (const msg of this.messages) {
            const dayKey = this._dayKey(msg.create_date);
            if (dayKey && dayKey !== lastDayKey) {
                items.push({ type: "divider", key: `divider-${dayKey}`, label: this._dayLabel(msg.create_date) });
                lastDayKey = dayKey;
            }
            items.push({ type: "message", key: `msg-${msg.id}`, msg });
        }
        return items;
    }

    // ---------- header / sidebar data ----------

    get conversationState() {
        return this.props.record.data.state;
    }

    get canReply() {
        return this.conversationState === "human_handling";
    }

    get stateInfo() {
        return STATE_INFO[this.conversationState] || { label: this.conversationState || "", cls: "secondary" };
    }

    get customerTitle() {
        const partnerLabel = this._many2oneLabel(this.props.record.data.partner_id);
        return partnerLabel || this.props.record.data.partner_phone || "Unknown customer";
    }

    get customerPhone() {
        return this.props.record.data.partner_phone || "";
    }

    get customerInitial() {
        const title = this.customerTitle || "?";
        return title.trim().charAt(0).toUpperCase();
    }

    get assignedUserLabel() {
        return this._many2oneLabel(this.props.record.data.assigned_user_id) || "Unassigned";
    }

    get handoffReason() {
        return this.props.record.data.handoff_reason || "";
    }

    get lastMessageAtLabel() {
        return this._formatDateTime(this.props.record.data.last_message_at);
    }

    get unreadCount() {
        return this.props.record.data.unread_count || 0;
    }

    get phoneNumberId() {
        return this.props.record.data.phone_number_id || "";
    }

    // ---------- helpers ----------

    _many2oneLabel(value) {
        if (!value) {
            return "";
        }
        if (Array.isArray(value)) {
            return value[1] || "";
        }
        if (typeof value === "object") {
            return value.display_name || value.name || "";
        }
        return String(value);
    }

    _isLuxon(dt) {
        return dt && typeof dt.toFormat === "function";
    }

    _dayKey(dt) {
        if (this._isLuxon(dt)) {
            return dt.toFormat("yyyy-MM-dd");
        }
        return dt ? String(dt).slice(0, 10) : null;
    }

    _dayLabel(dt) {
        if (!this._isLuxon(dt)) {
            return this._dayKey(dt) || "";
        }
        const now = luxon.DateTime.now();
        if (dt.hasSame(now, "day")) {
            return "Today";
        }
        if (dt.hasSame(now.minus({ days: 1 }), "day")) {
            return "Yesterday";
        }
        return dt.toFormat(dt.hasSame(now, "year") ? "d MMMM" : "d MMMM yyyy");
    }

    formatTime(dt) {
        if (this._isLuxon(dt)) {
            return dt.toFormat("HH:mm");
        }
        return dt ? String(dt) : "";
    }

    _formatDateTime(dt) {
        if (this._isLuxon(dt)) {
            return dt.toFormat("d MMM, HH:mm");
        }
        return dt ? String(dt) : "—";
    }

    senderLabel(msg) {
        switch (msg.sender_type) {
            case "employee":
                return "You";
            case "ai":
                return "AI";
            case "customer":
                return this.customerTitle;
            case "broadcast":
                return "Broadcast";
            case "system":
                return "System";
            default:
                return msg.sender_type;
        }
    }

    // ---------- header actions ----------

    async takeOver() {
        await this._runAction("action_take_over");
    }

    async resumeAi() {
        await this._runAction("action_resume_ai");
    }

    async closeConversation() {
        await this._runAction("action_close");
    }

    async _runAction(methodName) {
        if (this.state.actionPending) {
            return;
        }
        this.state.actionPending = true;
        try {
            await this.orm.call("whatsapp.conversation", methodName, [[this.props.record.resId]]);
            await this.props.record.load();
        } finally {
            this.state.actionPending = false;
        }
    }

    // ---------- composer ----------

    onDraftInput(ev) {
        this.state.draft = ev.target.value;
        this._autoResize();
    }

    _autoResize() {
        const el = this.textareaRef.el;
        if (!el) {
            return;
        }
        el.style.height = "auto";
        el.style.height = `${Math.min(el.scrollHeight, 120)}px`;
    }

    onDraftKeydown(ev) {
        // Enter sends, Shift+Enter adds a newline.
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendReply();
        }
    }

    async sendReply() {
        const body = this.state.draft.trim();
        if (!body || this.state.sending || !this.canReply) {
            return;
        }
        this.state.sending = true;
        try {
            await this.orm.call(
                "whatsapp.conversation",
                "action_send_employee_reply",
                [[this.props.record.resId], body]
            );
            this.state.draft = "";
            if (this.textareaRef.el) {
                this.textareaRef.el.style.height = "auto";
            }
            await this.props.record.load();
        } finally {
            this.state.sending = false;
        }
    }
}

registry.category("fields").add("whatsapp_thread", {
    component: WhatsappThread,
    supportedTypes: ["one2many"],
    // No <list>/<kanban> sub-arch is given for conversation_message_ids in the
    // view, so without this Odoo only fetches `id` for each related record —
    // every field the template reads below (body, direction, sender_type,
    // create_date) has to be declared here or it silently comes back empty.
    relatedFields: () => [
        { name: "direction", type: "selection" },
        { name: "sender_type", type: "selection" },
        { name: "body", type: "text" },
        { name: "create_date", type: "datetime" },
    ],
    // unread_count isn't placed anywhere else in the form view, so it has to
    // be requested explicitly or the sidebar would silently show 0 always.
    fieldDependencies: [{ name: "unread_count", type: "integer" }],
});