from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.test import TestCase
from django.utils import timezone

from .models import (
    Company,
    Contact,
    Deal,
    DealStage,
    Interaction,
    InteractionDirection,
    InteractionType,
    LeadStatus,
    LossReason,
    Task,
    TaskPriority,
    TaskStatus,
)

User = get_user_model()


class CoreModelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="commercial1",
            email="commercial1@example.com",
            password="securepassword123",
        )
        self.company = Company.objects.create(
            name="Acme Corp",
            domain="acme.com",
            industry="Technologie",
            owner=self.user,
            custom_fields={"taille_parc": 45, "type_licence": "Enterprise"},
        )
        self.contact = Contact.objects.create(
            first_name="Jean",
            last_name="Dupont",
            email="jean.dupont@acme.com",
            phone="+33612345678",
            job_title="Directeur Technique",
            company=self.company,
            owner=self.user,
            lead_status=LeadStatus.QUALIFIED,
            lead_score=85,
            custom_fields={"canal_acquisition": "LinkedIn"},
        )

    def test_company_creation_and_custom_fields(self):
        self.assertEqual(str(self.company), "Acme Corp")
        self.assertEqual(self.company.custom_fields.get("taille_parc"), 45)
        self.assertEqual(self.company.custom_fields.get("type_licence"), "Enterprise")

    def test_contact_full_name_and_str(self):
        self.assertEqual(self.contact.full_name, "Jean Dupont")
        self.assertEqual(str(self.contact), "Jean Dupont (Acme Corp)")

    def test_contact_email_uniqueness_and_null_handling(self):
        # 1. Duplication exacte -> doit échouer
        duplicate_contact = Contact(
            first_name="Autre",
            last_name="Personne",
            email="jean.dupont@acme.com",
        )
        with self.assertRaises(ValidationError):
            duplicate_contact.save()

        # 2. Insensibilité à la casse ("JEAN.DUPONT@ACME.COM") -> doit échouer
        case_duplicate = Contact(
            first_name="Autre",
            last_name="Personne",
            email="  JEAN.DUPONT@acme.COM  ",
        )
        with self.assertRaises(ValidationError):
            case_duplicate.save()

        # 3. Plusieurs contacts sans email (None ou "") -> autorisés sans conflit d'unicité
        c1 = Contact.objects.create(first_name="Sans", last_name="Email1", email="")
        c2 = Contact.objects.create(first_name="Sans", last_name="Email2", email=None)
        self.assertIsNone(c1.email)
        self.assertIsNone(c2.email)

    def test_protected_deletion_on_contact_and_company(self):
        # Interaction rattachée au contact et à l'entreprise
        Interaction.objects.create(
            type=InteractionType.NOTE,
            title="Note stratégique",
            contact=self.contact,
            company=self.company,
        )

        # La suppression directe du contact ou de l'entreprise doit être bloquée (PROTECT)
        with self.assertRaises(models.ProtectedError):
            self.contact.delete()

        with self.assertRaises(models.ProtectedError):
            self.company.delete()

        # Idem pour une tâche
        new_company = Company.objects.create(name="Beta SAS")
        new_contact = Contact.objects.create(first_name="Paul", last_name="Roc", company=new_company)
        Task.objects.create(
            title="Relancer devis",
            due_date=timezone.now() + timedelta(days=2),
            contact=new_contact,
            company=new_company,
        )

        with self.assertRaises(models.ProtectedError):
            new_contact.delete()

        with self.assertRaises(models.ProtectedError):
            new_company.delete()

    def test_deal_validation_requires_loss_reason_when_lost(self):
        # Création directe via save() sans motif de perte -> rejeté
        deal = Deal(
            title="Contrat Cloud Acme",
            amount=Decimal("15000.00"),
            stage=DealStage.LOST,
            contact=self.contact,
            company=self.company,
            owner=self.user,
        )
        with self.assertRaises(ValidationError) as ctx:
            deal.save()
        self.assertIn("loss_reason", ctx.exception.message_dict)

        # Avec motif de perte -> validation OK
        deal.loss_reason = LossReason.PRICE
        deal.loss_notes = "Budget gelé pour l'année en cours."
        deal.save()
        self.assertEqual(deal.stage, DealStage.LOST)
        self.assertIsNotNone(deal.closed_at)

    def test_deal_weighted_amount_and_pipeline_aggregation(self):
        deal1 = Deal.objects.create(
            title="Projet Audit",
            amount=Decimal("10000.00"),
            stage=DealStage.PROPOSAL,
            probability=50,
            contact=self.contact,
            company=self.company,
            owner=self.user,
        )
        deal2 = Deal.objects.create(
            title="Formation Dev",
            amount=Decimal("5000.00"),
            stage=DealStage.NEGOTIATION,
            probability=80,
            contact=self.contact,
            company=self.company,
            owner=self.user,
        )
        self.assertEqual(deal1.weighted_amount, Decimal("5000.00"))
        self.assertEqual(deal2.weighted_amount, Decimal("4000.00"))

        total = Deal.objects.total_pipeline_amount()
        weighted = Deal.objects.weighted_pipeline_amount()
        self.assertEqual(total, Decimal("15000.00"))
        self.assertEqual(weighted, Decimal("9000.00"))

    def test_interaction_validation_requires_target_entity(self):
        interaction = Interaction(
            type=InteractionType.CALL,
            title="Appel de qualification sans cible",
        )
        with self.assertRaises(ValidationError):
            interaction.save()

    def test_interaction_updates_last_activity_on_targets(self):
        self.assertIsNone(self.contact.last_activity_at)
        self.assertIsNone(self.company.last_activity_at)

        now = timezone.now()
        interaction = Interaction.objects.create(
            type=InteractionType.CALL,
            direction=InteractionDirection.OUTBOUND,
            title="Point de cadrage",
            content="Discussion très positive sur les besoins CRM.",
            contact=self.contact,
            company=self.company,
            user=self.user,
            occurred_at=now,
        )

        self.contact.refresh_from_db()
        self.company.refresh_from_db()

        self.assertIsNotNone(self.contact.last_activity_at)
        self.assertIsNotNone(self.company.last_activity_at)
        self.assertEqual(
            self.contact.last_activity_at.strftime("%Y-%m-%d %H:%M:%S"),
            now.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def test_bulk_create_and_sync_interactions(self):
        """Teste l'insertion en masse et la synchronisation groupée sans N+1 requêtes."""
        now = timezone.now()
        t1 = now - timedelta(hours=2)
        t2 = now  # plus récente

        interactions = [
            Interaction(
                type=InteractionType.NOTE,
                title="Premier contact importé",
                contact=self.contact,
                company=self.company,
                occurred_at=t1,
            ),
            Interaction(
                type=InteractionType.CALL,
                title="Second appel importé",
                contact=self.contact,
                company=self.company,
                occurred_at=t2,
            ),
        ]

        Interaction.objects.bulk_create_and_sync(interactions)

        self.contact.refresh_from_db()
        self.company.refresh_from_db()

        # Doit être aligné sur la date la plus récente t2
        self.assertEqual(
            self.contact.last_activity_at.strftime("%Y-%m-%d %H:%M:%S"),
            t2.strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Une ancienne interaction ne doit pas rétrograder la date
        t_old = now - timedelta(days=10)
        old_interaction = [
            Interaction(
                type=InteractionType.EMAIL,
                title="Ancien email importé",
                contact=self.contact,
                occurred_at=t_old,
            )
        ]
        Interaction.objects.bulk_create_and_sync(old_interaction)
        self.contact.refresh_from_db()
        self.assertEqual(
            self.contact.last_activity_at.strftime("%Y-%m-%d %H:%M:%S"),
            t2.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def test_contact_querysets(self):
        hot_leads = Contact.objects.hot_leads()
        self.assertIn(self.contact, hot_leads)

        needs_follow_up = Contact.objects.needs_follow_up(days=7)
        self.assertIn(self.contact, needs_follow_up)

        self.contact.last_activity_at = timezone.now()
        self.contact.save()
        self.assertNotIn(self.contact, Contact.objects.needs_follow_up(days=7))

        old_contact = Contact.objects.create(
            first_name="Alice",
            last_name="Martin",
            last_activity_at=timezone.now() - timedelta(days=45),
        )
        needs_follow_up_old = Contact.objects.needs_follow_up(days=30)
        self.assertIn(old_contact, needs_follow_up_old)

    def test_task_lifecycle_and_due_date_filters(self):
        past_date = timezone.now() - timedelta(days=1)
        overdue_task = Task.objects.create(
            title="Rappeler pour signer devis",
            due_date=past_date,
            priority=TaskPriority.HIGH,
            contact=self.contact,
            assigned_to=self.user,
        )

        overdue_tasks = Task.objects.overdue()
        self.assertIn(overdue_task, overdue_tasks)

        overdue_task.mark_completed()
        overdue_task.refresh_from_db()
        self.assertEqual(overdue_task.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(overdue_task.completed_at)
        self.assertNotIn(overdue_task, Task.objects.overdue())

    def test_with_related_optimization(self):
        contacts = list(Contact.objects.with_related().filter(id=self.contact.id))
        with self.assertNumQueries(0):
            _ = contacts[0].company.name
            _ = contacts[0].owner.username
