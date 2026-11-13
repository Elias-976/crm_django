from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .querysets import (
    CompanyQuerySet,
    ContactQuerySet,
    DealQuerySet,
    InteractionQuerySet,
    TaskQuerySet,
)


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


class Company(TimeStampedModel):
    """Représente une organisation ou un compte professionnel."""

    name = models.CharField(
        max_length=255,
        db_index=True,
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
        db_index=True,
        verbose_name="Dernière activité",
    )

    objects = CompanyQuerySet.as_manager()

    class Meta:
        verbose_name = "Entreprise"
        verbose_name_plural = "Entreprises"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class LeadStatus(models.TextChoices):
    NEW = "NEW", "Nouveau prospect"
    CONTACTED = "CONTACTED", "Contacté"
    QUALIFIED = "QUALIFIED", "Qualifié"
    UNQUALIFIED = "UNQUALIFIED", "Non qualifié"
    CUSTOMER = "CUSTOMER", "Client"


class Contact(TimeStampedModel):
    """Représente une personne physique (prospect, client, partenaire)."""

    first_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Prénom",
    )
    last_name = models.CharField(
        max_length=100,
        db_index=True,
        verbose_name="Nom",
    )
    email = models.EmailField(
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        verbose_name="E-mail",
    )
    phone = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
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
        db_index=True,
        verbose_name="Statut du lead",
    )
    lead_score = models.IntegerField(
        default=0,
        db_index=True,
        verbose_name="Score du lead",
        help_text="Score de qualification de 0 à 100+",
    )
    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
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
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["lead_status", "lead_score"]),
        ]

    def clean(self):
        super().clean()
        if self.email:
            cleaned_email = self.email.strip().lower()
            self.email = cleaned_email if cleaned_email else None
        else:
            self.email = None

    def save(self, *args, **kwargs):
        self.clean()
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name if name else "Sans nom"

    def __str__(self) -> str:
        if self.company:
            return f"{self.full_name} ({self.company.name})"
        return self.full_name


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


class Deal(TimeStampedModel):
    """Représente une transaction ou opportunité commerciale dans le pipeline."""

    title = models.CharField(
        max_length=255,
        db_index=True,
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
        db_index=True,
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
        db_index=True,
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
        db_index=True,
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
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(stage=DealStage.LOST) | ~models.Q(loss_reason=""),
                name="deal_lost_must_have_loss_reason",
            )
        ]

    def clean(self):
        super().clean()
        if self.stage == DealStage.LOST and not self.loss_reason:
            raise ValidationError({
                "loss_reason": "Le motif de perte est obligatoire lorsqu'une opportunité est marquée comme Perdue."
            })
        if self.stage in [DealStage.WON, DealStage.LOST] and not self.closed_at:
            self.closed_at = timezone.now()
        elif self.stage not in [DealStage.WON, DealStage.LOST] and self.closed_at:
            self.closed_at = None

    def save(self, *args, **kwargs):
        self.clean()
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def weighted_amount(self) -> Decimal:
        """Montant pondéré pour le calcul du prévisionnel."""
        return (self.amount * Decimal(self.probability) / Decimal(100)).quantize(Decimal("0.01"))

    def __str__(self) -> str:
        return f"{self.title} ({self.amount} €)"


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


class Interaction(TimeStampedModel):
    """Événement ou interaction consigné dans la timeline chronologique."""

    type = models.CharField(
        max_length=20,
        choices=InteractionType.choices,
        default=InteractionType.NOTE,
        db_index=True,
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
        db_index=True,
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

    def save(self, *args, **kwargs):
        self.clean()
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


class TaskPriority(models.TextChoices):
    LOW = "LOW", "Basse"
    MEDIUM = "MEDIUM", "Moyenne"
    HIGH = "HIGH", "Haute"
    URGENT = "URGENT", "Urgente"


class TaskStatus(models.TextChoices):
    PENDING = "PENDING", "À faire"
    COMPLETED = "COMPLETED", "Terminée"
    CANCELLED = "CANCELLED", "Annulée"


class Task(TimeStampedModel):
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
        db_index=True,
        verbose_name="Date d'échéance",
    )
    priority = models.CharField(
        max_length=10,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
        db_index=True,
        verbose_name="Priorité",
    )
    status = models.CharField(
        max_length=15,
        choices=TaskStatus.choices,
        default=TaskStatus.PENDING,
        db_index=True,
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

    def save(self, *args, **kwargs):
        self.clean()
        self.full_clean()
        super().save(*args, **kwargs)

    def mark_completed(self):
        """Marque la tâche comme terminée avec horodatage."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "completed_at", "updated_at"])

    def __str__(self) -> str:
        return f"{self.title} (Échéance: {self.due_date:%d/%m/%Y})"
