# Odoo WhatsApp Bridge

Custom Odoo module for integrating Odoo with a WhatsApp-based AI customer communication system.

## Features

* WhatsApp configuration
* AI configuration
* AI customer conversations
* Human agent takeover
* Conversation history
* Knowledge and RAG management
* WhatsApp notification templates
* WhatsApp broadcast campaigns
* Broadcast recipient management
* Payment-related WhatsApp workflows
* Odoo ↔ FastAPI integration

## Architecture

```text
Customer
   ↓
WhatsApp
   ↓
Meta WhatsApp Cloud API
   ↓
FastAPI
   ↓
Odoo WhatsApp Bridge
   ↓
Odoo Business Data
```

FastAPI handles WhatsApp and Meta API communication, while Odoo manages business data, configuration, workflows, and conversation records.

## Main Models

* `whatsapp.config`
* `whatsapp.ai.config`
* `whatsapp.bridge`
* `whatsapp.conversation`
* `whatsapp.conversation.message`
* `whatsapp.knowledge.article`
* `whatsapp.notification.template`
* `whatsapp.broadcast.campaign`
* `whatsapp.broadcast.recipient`

## Conversation Management

Employees can view customer conversations and take over conversations from the AI.

Conversation states:

* AI Handling
* Pending Human
* Human Handling
* Closed

## Notification Templates

Configurable WhatsApp templates supporting:

* Body variables
* Headers
* Footer
* Buttons
* Meta template information
* Meta approval status

## Broadcasts

Broadcast campaigns can target customers using configurable criteria.

The system supports:

* Normal messages during the active customer-service window
* Approved Meta templates outside the active window

## Knowledge / RAG

Knowledge articles can be configured in Odoo and used by the AI system to provide business-specific responses.

## Security

Never commit:

* API tokens
* Access tokens
* Internal secrets
* Production credentials
* `.env` files

## Status

Core WhatsApp AI, conversation handling, human takeover, knowledge/RAG, notifications, broadcasts, media/document processing, and payment workflows are implemented.

Future improvements include richer media messaging and additional WhatsApp features.

## License

Private project.
