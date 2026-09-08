{
    "name": "Enz WhatsApp Bridge",
    "version": "18.0.1.0.0",
    "summary": "Backend methods exposed to the FastAPI WhatsApp/AI service",
    "category": "Sales",
    'author': "ForGe/ Anirudh M",
    'website': "https://",
    'license': 'LGPL-3',
    "depends": ["base", "sale", "stock"],
    "data": [
        'security/ir.model.access.csv',
        'data/whatsapp_broadcast_cron.xml',
        "views/whatsapp_config_views.xml",
        "views/whatsapp_knowledge_views.xml",
        "views/whatsapp_ai_config_views.xml",
        "views/whatsapp_notification_template_views.xml",
        "views/whatsapp_conversation_views.xml",
        "views/whatsapp_broadcast_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "enz_whatsapp_bridge/static/src/js/whatsapp_thread.js",
            "enz_whatsapp_bridge/static/src/xml/whatsapp_thread.xml",
            "enz_whatsapp_bridge/static/src/scss/whatsapp_thread.scss",
        ],
    },
    "installable": True,
    "application": False,
}