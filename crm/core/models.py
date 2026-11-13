import uuid
from decimal import Decimal
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Lower
from django.utils import timezone

from .querysets import (
    CompanyQuerySet,
    ContactQuerySet,
    DealQuerySet,
    InteractionQuerySet,
    TaskQuerySet,
)


# ------------------------------------------------------------------------------
# 1. Classes de Base Abstraites (Horodatage, Corbeille & Multi-Tenant)
# ------------------------------------------------------------------------------

class TimeStampedModel(models.Model):
    """Classe de base abstraite apportant l'horodatage standardisé."""

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name="Créé le",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Modifié le",
    )

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Classe abstraite fournissant la suppression douce (corbeille / résilience des données)."""

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Date de suppression",
    )

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self):
        """Marque l'enregistrement comme supprimé sans le détruire en base."""
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    def restore(self):
        """Restaure un enregistrement depuis la corbeille."""
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"])

    def delete(self, using=None, keep_parents=False, soft=False):
        """
        Supprime l'objet. Si soft=True, bascule vers une suppression douce (corbeille).
        Par défaut, conserve la suppression standard Django avec respect strict
        de l'intégrité référentielle (on_delete=models.PROTECT).
        """
        if soft:
            return self.soft_delete()
        return super().delete(using=using, keep_parents=keep_parents)


class Organization(TimeStampedModel, SoftDeleteModel):
    """Représente un espace client / tenant isolé dans le CRM SaaS."""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
        verbose_name="Identifiant public (UUID)",
    )
    name = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name="Nom de l'organisation",
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        verbose_name="Identifiant URL / Slug",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Actif",
    )

    class Meta:
        verbose_name = "Organisation"
        verbose_name_plural = "Organisations"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class OrganizationRole(models.TextChoices):
    OWNER = "OWNER", "Propriétaire"
    ADMIN = "ADMIN", "Administrateur"
    MEMBER = "MEMBER", "Collaborateur"
    VIEWER = "VIEWER", "Lecture seule"


class UserProfile(TimeStampedModel):
    """Profil utilisateur liant AUTH_USER_MODEL à son organisation active/courante."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="Utilisateur",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="user_profiles",
        verbose_name="Espace de travail actif",
    )
    role = models.CharField(
        max_length=20,
        choices=OrganizationRole.choices,
        default=OrganizationRole.MEMBER,
        db_index=True,
        verbose_name="Rôle",
    )

    class Meta:
        verbose_name = "Profil utilisateur"
        verbose_name_plural = "Profils utilisateurs"
        indexes = [
            models.Index(fields=["organization", "role"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} ({self.organization.name})"


class TenantModel(TimeStampedModel, SoftDeleteModel):
    """Classe de base abstraite assurant le cloisonnement multi-tenant des données."""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
        verbose_name="Identifiant public (UUID)",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        db_index=True,
        related_name="%(class)s_set",
        verbose_name="Organisation",
    )

    class Meta:
        abstract = True


class OrganizationMembership(TenantModel):
    """Association permettant à un utilisateur d'appartenir à plusieurs Organisations (Multi-Workspace SaaS)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Utilisateur",
    )
    role = models.CharField(
        max_length=20,
        choices=OrganizationRole.choices,
        default=OrganizationRole.MEMBER,
        db_index=True,
        verbose_name="Rôle dans l'organisation",
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name="Espace par défaut",
    )

    class Meta:
        verbose_name = "Membre d'organisation"
        verbose_name_plural = "Membres d'organisation"
        indexes = [
            models.Index(fields=["organization", "role"]),
            models.Index(fields=["user", "is_default"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="unique_user_per_organization",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.organization.name} ({self.get_role_display()})"


# ------------------------------------------------------------------------------
# 2. Fiches Entreprises & Comptes
# ------------------------------------------------------------------------------

class Company(TenantModel):
    """Représente une organisation ou un compte professionnel."""

    name = models.CharField(
        max_length=255,
        verbose_name="Nom de l'entreprise",
    )
    domain = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Site web / Domaine",
    )
    phone = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Téléphone",
    )
    industry = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Secteur d'activité",
    )
    address = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Adresse",
    )
    postal_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Code postal",
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Ville",
    )
    country = models.CharField(
        max_length=100,
        blank=True,
        default="France",
        verbose_name="Pays",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="companies",
        verbose_name="Responsable",
    )
    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Champs personnalisés",
        help_text="Métadonnées spécifiques au secteur (JSON)",
    )
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Dernière activité",
    )

    objects = CompanyQuerySet.as_manager()

    class Meta:
        verbose_name = "Entreprise"
        verbose_name_plural = "Entreprises"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["organization", "name"]),
            models.Index(fields=["organization", "-last_activity_at"]),
            models.Index(fields=["organization", "owner"]),
            models.Index(fields=["organization", "deleted_at"]),
            GinIndex(
                fields=["custom_fields"],
                name="company_custom_fields_gin",
                opclasses=["jsonb_path_ops"],
            ),
        ]

    def save(self, *args, **kwargs):
        if "update_fields" not in kwargs:
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


# ------------------------------------------------------------------------------
# 3. Fiches Contacts & Prospects
# ------------------------------------------------------------------------------

class LeadStatus(models.TextChoices):
    NEW = "NEW", "Nouveau prospect"
    CONTACTED = "CONTACTED", "Contacté"
    QUALIFIED = "QUALIFIED", "Qualifié"
    UNQUALIFIED = "UNQUALIFIED", "Non qualifié"
    CUSTOMER = "CUSTOMER", "Client"


class Contact(TenantModel):
    """Représente une personne physique (prospect, client, partenaire)."""

    first_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Prénom",
    )
    last_name = models.CharField(
        max_length=100,
        verbose_name="Nom",
    )
    email = models.EmailField(
        blank=True,
        null=True,
        verbose_name="E-mail",
    )
    phone = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Téléphone / WhatsApp",
    )
    job_title = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Poste / Fonction",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contacts",
        verbose_name="Entreprise",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contacts",
        verbose_name="Responsable",
    )
    lead_status = models.CharField(
        max_length=20,
        choices=LeadStatus.choices,
        default=LeadStatus.NEW,
        verbose_name="Statut du lead",
    )
    lead_score = models.IntegerField(
        default=0,
        verbose_name="Score du lead",
        help_text="Score de qualification de 0 à 100+",
    )
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Dernière activité",
    )
    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Champs personnalisés",
    )

    objects = ContactQuerySet.as_manager()

    class Meta:
        verbose_name = "Contact"
        verbose_name_plural = "Contacts"
        ordering = ["-last_activity_at", "-created_at"]
        indexes = [
            models.Index(fields=["organization", "last_name", "first_name"]),
            models.Index(fields=["organization", "lead_status", "-lead_score"]),
            models.Index(fields=["organization", "-last_activity_at"]),
            models.Index(fields=["organization", "owner"]),
            models.Index(fields=["organization", "deleted_at"]),
            GinIndex(
                fields=["custom_fields"],
                name="contact_custom_fields_gin",
                opclasses=["jsonb_path_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                models.F("organization"),
                Lower("email"),
                condition=models.Q(email__isnull=False),
                name="unique_contact_email_per_organization",
            ),
        ]

    def clean_fields(self, exclude=None):
        if self.email and (exclude is None or "email" not in exclude):
            cleaned_email = self.email.strip().lower()
            self.email = cleaned_email if cleaned_email else None
        super().clean_fields(exclude=exclude)

    def clean(self):
        super().clean()
        if self.email:
            cleaned_email = self.email.strip().lower()
            self.email = cleaned_email if cleaned_email else None
        else:
            self.email = None

        if self.company_id and self.company.organization_id != self.organization_id:
            raise ValidationError({
                "company": "L'entreprise associée doit appartenir à la même organisation."
            })

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_status = None
        if not is_new:
            old_status = (
                Contact.objects.filter(pk=self.pk)
                .values_list("lead_status", flat=True)
                .first()
            )

        if "update_fields" not in kwargs:
            self.full_clean()
        super().save(*args, **kwargs)

        if old_status != self.lead_status and self.lead_status == LeadStatus.QUALIFIED:
            from .workflows import execute_contact_workflows

            execute_contact_workflows(self)

    @property
    def full_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name if name else "Sans nom"

    def __str__(self) -> str:
        if self.company:
            return f"{self.full_name} ({self.company.name})"
        return self.full_name


# ------------------------------------------------------------------------------
# 4. Pipeline Commercial & Opportunités (Deals)
# ------------------------------------------------------------------------------

class DealStage(models.TextChoices):
    LEAD = "LEAD", "Prospect identifié"
    QUALIFIED = "QUALIFIED", "Qualification"
    PROPOSAL = "PROPOSAL", "Démo / Offre"
    NEGOTIATION = "NEGOTIATION", "Négociation"
    WON = "WON", "Gagné"
    LOST = "LOST", "Perdu"


class LossReason(models.TextChoices):
    PRICE = "PRICE", "Prix trop élevé"
    TIMING = "TIMING", "Mauvais timing / Projet reporté"
    COMPETITOR = "COMPETITOR", "Choix d'un concurrent"
    FEATURE = "FEATURE", "Fonctionnalité / Besoin non couvert"
    NO_RESPONSE = "NO_RESPONSE", "Pas de réponse / Ghost"
    OTHER = "OTHER", "Autre motif"


class Deal(TenantModel):
    """Représente une transaction ou opportunité commerciale dans le pipeline."""

    title = models.CharField(
        max_length=255,
        verbose_name="Intitulé de l'opportunité",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Montant (€)",
    )
    stage = models.CharField(
        max_length=20,
        choices=DealStage.choices,
        default=DealStage.LEAD,
        verbose_name="Étape du pipeline",
    )
    probability = models.PositiveSmallIntegerField(
        default=10,
        verbose_name="Probabilité (%)",
        help_text="Pourcentage de chance de conversion (0-100)",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deals",
        verbose_name="Contact principal",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deals",
        verbose_name="Entreprise",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deals",
        verbose_name="Responsable",
    )
    expected_close_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Date de clôture estimée",
    )
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Date de clôture effective",
    )
    loss_reason = models.CharField(
        max_length=30,
        choices=LossReason.choices,
        blank=True,
        verbose_name="Motif de perte",
    )
    loss_notes = models.TextField(
        blank=True,
        verbose_name="Explications sur la perte",
    )
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Dernière activité",
    )
    custom_fields = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Champs personnalisés",
    )

    objects = DealQuerySet.as_manager()

    class Meta:
        verbose_name = "Opportunité"
        verbose_name_plural = "Opportunités"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["organization", "stage", "-updated_at"]),
            models.Index(fields=["organization", "owner", "stage"]),
            models.Index(fields=["organization", "expected_close_date"]),
            models.Index(fields=["organization", "deleted_at"]),
            GinIndex(
                fields=["custom_fields"],
                name="deal_custom_fields_gin",
                opclasses=["jsonb_path_ops"],
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(stage=DealStage.LOST) | ~models.Q(loss_reason=""),
                name="deal_lost_must_have_loss_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(probability__lte=100),
                name="deal_probability_max_100",
            ),
        ]

    def clean(self):
        super().clean()
        if self.company_id and self.company.organization_id != self.organization_id:
            raise ValidationError({
                "company": "L'entreprise associée doit appartenir à la même organisation."
            })
        if self.contact_id and self.contact.organization_id != self.organization_id:
            raise ValidationError({
                "contact": "Le contact associé doit appartenir à la même organisation."
            })
        if self.stage == DealStage.LOST and not self.loss_reason:
            raise ValidationError({
                "loss_reason": "Le motif de perte est obligatoire lorsqu'une opportunité est marquée comme Perdue."
            })
        if self.stage in [DealStage.WON, DealStage.LOST] and not self.closed_at:
            self.closed_at = timezone.now()
        elif self.stage not in [DealStage.WON, DealStage.LOST] and self.closed_at:
            self.closed_at = None

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_stage = None
        if not is_new:
            old_stage = (
                Deal.objects.filter(pk=self.pk)
                .values_list("stage", flat=True)
                .first()
            )

        if "update_fields" not in kwargs:
            self.full_clean()
        super().save(*args, **kwargs)

        if old_stage != self.stage:
            from .workflows import execute_deal_workflows

            new_stage = self.stage
            execute_deal_workflows(self, old_stage=old_stage, new_stage=new_stage)

    @property
    def weighted_amount(self) -> Decimal:
        """Montant pondéré pour le calcul du prévisionnel."""
        return (self.amount * Decimal(self.probability) / Decimal(100)).quantize(Decimal("0.01"))

    def __str__(self) -> str:
        return f"{self.title} ({self.amount} €)"


# ------------------------------------------------------------------------------
# 5. Timeline des Interactions Centralisée
# ------------------------------------------------------------------------------

class InteractionType(models.TextChoices):
    NOTE = "NOTE", "Note interne"
    CALL = "CALL", "Appel téléphonique"
    EMAIL = "EMAIL", "E-mail"
    MEETING = "MEETING", "Rendez-vous / Réunion"
    WHATSAPP = "WHATSAPP", "WhatsApp / Messagerie"
    OTHER = "OTHER", "Autre interaction"


class InteractionDirection(models.TextChoices):
    INBOUND = "INBOUND", "Entrant"
    OUTBOUND = "OUTBOUND", "Sortant"
    NOT_APPLICABLE = "NA", "Non applicable"


class Interaction(TenantModel):
    """Événement ou interaction consigné dans la timeline chronologique."""

    type = models.CharField(
        max_length=20,
        choices=InteractionType.choices,
        default=InteractionType.NOTE,
        verbose_name="Type d'interaction",
    )
    direction = models.CharField(
        max_length=10,
        choices=InteractionDirection.choices,
        default=InteractionDirection.NOT_APPLICABLE,
        verbose_name="Direction",
    )
    title = models.CharField(
        max_length=255,
        verbose_name="Objet / Titre",
    )
    content = models.TextField(
        blank=True,
        verbose_name="Détails / Compte-rendu",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="interactions",
        verbose_name="Contact",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="interactions",
        verbose_name="Entreprise",
    )
    deal = models.ForeignKey(
        Deal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interactions",
        verbose_name="Opportunité",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interactions",
        verbose_name="Auteur",
    )
    occurred_at = models.DateTimeField(
        default=timezone.now,
        verbose_name="Date et heure de l'interaction",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Métadonnées techniques",
        help_text="Durée d'appel, message_id externe, canal, etc.",
    )

    objects = InteractionQuerySet.as_manager()

    class Meta:
        verbose_name = "Interaction"
        verbose_name_plural = "Interactions"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["organization", "-occurred_at"]),
            models.Index(fields=["organization", "contact", "-occurred_at"]),
            models.Index(fields=["organization", "company", "-occurred_at"]),
            models.Index(fields=["organization", "deal", "-occurred_at"]),
            models.Index(fields=["organization", "deleted_at"]),
            GinIndex(
                fields=["metadata"],
                name="interaction_metadata_gin",
                opclasses=["jsonb_path_ops"],
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(contact__isnull=False)
                    | models.Q(company__isnull=False)
                    | models.Q(deal__isnull=False)
                ),
                name="interaction_requires_target_entity",
            )
        ]

    def clean(self):
        super().clean()
        if not self.contact_id and not self.company_id and not self.deal_id:
            raise ValidationError(
                "Une interaction doit être rattachée à au moins un Contact, une Entreprise ou une Opportunité."
            )
        if self.contact_id and self.contact.organization_id != self.organization_id:
            raise ValidationError({
                "contact": "Le contact associé doit appartenir à la même organisation."
            })
        if self.company_id and self.company.organization_id != self.organization_id:
            raise ValidationError({
                "company": "L'entreprise associée doit appartenir à la même organisation."
            })
        if self.deal_id and self.deal.organization_id != self.organization_id:
            raise ValidationError({
                "deal": "L'opportunité associée doit appartenir à la même organisation."
            })

    def save(self, *args, **kwargs):
        if "update_fields" not in kwargs:
            self.full_clean()
        super().save(*args, **kwargs)

    def _sync_last_activity(self):
        """Méthode d'aide pour déclencher la synchronisation directe si besoin."""
        from .services import sync_entities_last_activity

        sync_entities_last_activity(
            contact_ids=[self.contact_id] if self.contact_id else None,
            company_ids=[self.company_id] if self.company_id else None,
            deal_ids=[self.deal_id] if self.deal_id else None,
            target_time=self.occurred_at or timezone.now(),
        )

    def __str__(self) -> str:
        return f"[{self.get_type_display()}] {self.title} ({self.occurred_at:%d/%m/%Y %H:%M})"


# ------------------------------------------------------------------------------
# 6. Tâches & Relances (Productivité)
# ------------------------------------------------------------------------------

class TaskPriority(models.TextChoices):
    LOW = "LOW", "Basse"
    MEDIUM = "MEDIUM", "Moyenne"
    HIGH = "HIGH", "Haute"
    URGENT = "URGENT", "Urgente"


class TaskStatus(models.TextChoices):
    PENDING = "PENDING", "À faire"
    COMPLETED = "COMPLETED", "Terminée"
    CANCELLED = "CANCELLED", "Annulée"


class Task(TenantModel):
    """Tâche ou rappel de relance commerciale lié aux fiches clients."""

    title = models.CharField(
        max_length=255,
        verbose_name="Titre du rappel / de la tâche",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Instructions / Détails",
    )
    due_date = models.DateTimeField(
        verbose_name="Date d'échéance",
    )
    priority = models.CharField(
        max_length=10,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
        verbose_name="Priorité",
    )
    status = models.CharField(
        max_length=15,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
        verbose_name="Statut",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Date de réalisation",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="Assigné à",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="Contact",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="Entreprise",
    )
    deal = models.ForeignKey(
        Deal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="Opportunité",
    )

    objects = TaskQuerySet.as_manager()

    class Meta:
        verbose_name = "Tâche"
        verbose_name_plural = "Tâches"
        ordering = ["due_date", "-priority"]
        indexes = [
            models.Index(fields=["organization", "status", "due_date"]),
            models.Index(fields=["organization", "assigned_to", "status", "due_date"]),
            models.Index(fields=["organization", "deleted_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(contact__isnull=False)
                    | models.Q(company__isnull=False)
                    | models.Q(deal__isnull=False)
                ),
                name="task_requires_target_entity",
            )
        ]

    def clean(self):
        super().clean()
        if not self.contact_id and not self.company_id and not self.deal_id:
            raise ValidationError(
                "Une tâche doit être rattachée à au moins un Contact, une Entreprise ou une Opportunité."
            )
        if self.contact_id and self.contact.organization_id != self.organization_id:
            raise ValidationError({
                "contact": "Le contact associé doit appartenir à la même organisation."
            })
        if self.company_id and self.company.organization_id != self.organization_id:
            raise ValidationError({
                "company": "L'entreprise associée doit appartenir à la même organisation."
            })
        if self.deal_id and self.deal.organization_id != self.organization_id:
            raise ValidationError({
                "deal": "L'opportunité associée doit appartenir à la même organisation."
            })

    def save(self, *args, **kwargs):
        if "update_fields" not in kwargs:
            self.full_clean()
        super().save(*args, **kwargs)

    def mark_completed(self, log_interaction: bool = False):
        """Marque la tâche comme terminée avec horodatage et consigne optionnellement dans la timeline."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])
        if log_interaction and (self.contact_id or self.company_id or self.deal_id):
            Interaction.objects.create(
                organization_id=self.organization_id,
                type=InteractionType.NOTE,
                direction=InteractionDirection.NOT_APPLICABLE,
                title=f"Tâche terminée : {self.title}",
                content=f"Réalisée le {self.completed_at:%d/%m/%Y à %H:%M}."
                + (f"\n\nInstructions: {self.description}" if self.description else ""),
                contact=self.contact,
                company=self.company,
                deal=self.deal,
                user=self.assigned_to,
                occurred_at=self.completed_at,
            )

    def __str__(self) -> str:
        return f"{self.title} (Échéance: {self.due_date:%d/%m/%Y})"


# ------------------------------------------------------------------------------
# 7. Moteur de Règles d'Automatisation (Workflows)
# ------------------------------------------------------------------------------

class WorkflowTrigger(models.TextChoices):
    DEAL_STAGE_ENTERED = "DEAL_STAGE_ENTERED", "L'opportunité entre dans une étape spécifique"
    DEAL_WON = "DEAL_WON", "L'opportunité est Gagnée"
    DEAL_LOST = "DEAL_LOST", "L'opportunité est Perdue"
    CONTACT_QUALIFIED = "CONTACT_QUALIFIED", "Le prospect est Qualifié"


class WorkflowAction(models.TextChoices):
    CREATE_TASK = "CREATE_TASK", "Créer une tâche de relance automatique"
    SEND_EMAIL = "SEND_EMAIL", "Envoyer un e-mail modèle (Template)"
    CREATE_TASK_AND_EMAIL = "CREATE_TASK_AND_EMAIL", "Créer une tâche ET envoyer un e-mail"


class WorkflowRule(TenantModel):
    """Règle d'automatisation commerciale (Workflow) exécutée sur déclencheur événementiel."""

    name = models.CharField(
        max_length=255,
        verbose_name="Nom de la règle",
    )
    trigger_event = models.CharField(
        max_length=30,
        choices=WorkflowTrigger.choices,
        default=WorkflowTrigger.DEAL_STAGE_ENTERED,
        db_index=True,
        verbose_name="Événement déclencheur",
    )
    trigger_stage = models.CharField(
        max_length=20,
        choices=DealStage.choices,
        blank=True,
        db_index=True,
        verbose_name="Étape cible du pipeline",
        help_text="Requis si le déclencheur est une étape du pipeline (ex: Démo / Devis envoyé).",
    )
    action_type = models.CharField(
        max_length=30,
        choices=WorkflowAction.choices,
        default=WorkflowAction.CREATE_TASK,
        verbose_name="Action à exécuter",
    )
    task_title = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Titre de la tâche à créer",
        help_text="Variables: {deal_title}, {contact_name}, {company_name}",
    )
    task_delay_days = models.PositiveIntegerField(
        default=3,
        verbose_name="Délai d'échéance (en jours)",
        help_text="Ex: 3 pour planifier une relance à J+3",
    )
    task_priority = models.CharField(
        max_length=10,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
        verbose_name="Priorité de la tâche",
    )
    email_subject = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Objet de l'e-mail",
        help_text="Variables: {deal_title}, {contact_name}, {company_name}",
    )
    email_body = models.TextField(
        blank=True,
        verbose_name="Corps de l'e-mail",
        help_text="Contenu du message envoyé au contact rattaché.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Règle active",
    )

    class Meta:
        verbose_name = "Règle d'automatisation"
        verbose_name_plural = "Règles d'automatisation"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["organization", "is_active", "trigger_event"]),
            models.Index(fields=["organization", "deleted_at"]),
        ]

    def clean(self):
        super().clean()
        if self.trigger_event == WorkflowTrigger.DEAL_STAGE_ENTERED and not self.trigger_stage:
            raise ValidationError({
                "trigger_stage": "Veuillez spécifier l'étape cible pour ce déclencheur."
            })
        if self.action_type in [WorkflowAction.CREATE_TASK, WorkflowAction.CREATE_TASK_AND_EMAIL] and not self.task_title:
            self.task_title = "Relance : {deal_title}"

    def save(self, *args, **kwargs):
        if "update_fields" not in kwargs:
            self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.get_trigger_event_display()})"
