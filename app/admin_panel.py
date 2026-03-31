"""Interface d'administration web -- Tendo.

Utilise sqladmin pour fournir un panneau d'administration complet
sans code frontend. Accessible a /admin.
"""

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.config import settings


class TendoAuthBackend(AuthenticationBackend):
    """Authentification basique pour le panneau admin."""

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")
        if username == "admin" and password == settings.secret_key:
            request.session.update({"authenticated": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)


def setup_admin(app):
    """Configure et monte le panneau d'administration sur /admin."""
    from app.models.user import User
    from app.models.publication import Publication
    from app.models.subscription import Subscription
    from app.models.notification import Notification
    from app.models.email_tracking import EmailTracking
    from app.models.knowledge_base import KnowledgeBase
    from app.models.document_analysis import DocumentAnalysis
    from app.utils.db import async_engine

    # -- Definir les vues avec les modeles reels --

    class UserAdmin(ModelView, model=User):
        column_list = [
            "id", "phone_number", "name", "company",
            "subscription_status", "subscription_plan",
            "is_active", "created_at", "trial_end",
        ]
        column_searchable_list = ["phone_number", "name", "company"]
        column_sortable_list = ["id", "created_at", "name", "subscription_status"]
        column_default_sort = ("created_at", True)
        name = "Utilisateur"
        name_plural = "Utilisateurs"
        icon = "fa-solid fa-users"
        page_size = 25

    class PublicationAdmin(ModelView, model=Publication):
        column_list = [
            "id", "source", "reference", "title",
            "category", "published_date", "deadline",
            "is_processed", "created_at",
        ]
        column_searchable_list = ["title", "reference", "source"]
        column_sortable_list = ["id", "created_at", "source", "deadline"]
        column_default_sort = ("created_at", True)
        name = "Publication"
        name_plural = "Publications"
        icon = "fa-solid fa-file-text"
        page_size = 25

    class SubscriptionAdmin(ModelView, model=Subscription):
        column_list = [
            "id", "user_id", "plan", "status",
            "amount", "start_date", "end_date",
        ]
        column_sortable_list = ["id", "start_date", "end_date", "status"]
        column_default_sort = ("start_date", True)
        name = "Abonnement"
        name_plural = "Abonnements"
        icon = "fa-solid fa-credit-card"
        page_size = 25

    class NotificationAdmin(ModelView, model=Notification):
        column_list = [
            "id", "user_id", "publication_id",
            "sent_at", "channel",
        ]
        column_sortable_list = ["id", "sent_at"]
        column_default_sort = ("sent_at", True)
        name = "Notification"
        name_plural = "Notifications"
        icon = "fa-solid fa-bell"
        page_size = 25

    class EmailTrackingAdmin(ModelView, model=EmailTracking):
        column_list = [
            "id", "user_id", "publication_id",
            "email_sent_to", "subject", "created_at",
        ]
        column_sortable_list = ["id", "created_at"]
        column_default_sort = ("created_at", True)
        name = "Email Suivi"
        name_plural = "Emails Suivi"
        icon = "fa-solid fa-envelope"
        page_size = 25

    # -- Creer l'admin --
    auth_backend = TendoAuthBackend(secret_key=settings.secret_key)

    admin = Admin(
        app,
        engine=async_engine,
        authentication_backend=auth_backend,
        title="Tendo Admin",
        base_url="/admin",
    )

    class KnowledgeBaseAdmin(ModelView, model=KnowledgeBase):
        column_list = [
            "id", "category", "subcategory", "title",
            "country", "language", "is_active", "created_at",
        ]
        column_searchable_list = ["title", "content", "category"]
        column_sortable_list = ["id", "created_at", "category"]
        column_default_sort = ("created_at", True)
        name = "Connaissance"
        name_plural = "Base de Connaissances"
        icon = "fa-solid fa-book"
        page_size = 25

    class DocumentAnalysisAdmin(ModelView, model=DocumentAnalysis):
        column_list = [
            "id", "source_type", "document_type", "document_subtype",
            "classification_confidence", "extraction_method",
            "page_count", "is_analyzed", "created_at",
        ]
        column_searchable_list = ["document_type", "original_filename"]
        column_sortable_list = ["id", "created_at", "document_type", "classification_confidence"]
        column_default_sort = ("created_at", True)
        name = "Analyse Document"
        name_plural = "Analyses Documents"
        icon = "fa-solid fa-magnifying-glass"
        page_size = 25

    admin.add_view(UserAdmin)
    admin.add_view(PublicationAdmin)
    admin.add_view(SubscriptionAdmin)
    admin.add_view(NotificationAdmin)
    admin.add_view(EmailTrackingAdmin)
    admin.add_view(KnowledgeBaseAdmin)
    admin.add_view(DocumentAnalysisAdmin)

    return admin
