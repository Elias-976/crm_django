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
    Organization,
    OrganizationMembership,
    OrganizationRole,
    Task,
    TaskPriority,
    TaskStatus,
    UserProfile,
    WorkflowAction,
    WorkflowRule,
    WorkflowTrigger,
)

User = get_user_model()


class CoreModelTestCase(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="SaaS Workspace Alpha",
            slug="workspace-alpha",
        )
        self.user = User.objects.create_user(
            username="commercial1",
            email="commercial1@example.com",
            password="securepassword123",
        )
        self.user_profile = UserProfile.objects.create(
            user=self.user,
            organization=self.organization,
            role="COMMERCIAL",
        )
        self.company = Company.objects.create(
            organization=self.organization,
            name="Acme Corp",
            domain="acme.com",
            industry="Technologie",
            owner=self.user,
            custom_fields={"taille_parc": 45, "type_licence": "Enterprise"},
        )
        self.contact = Contact.objects.create(
            organization=self.organization,
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

    def test_organization_and_user_profile(self):
        self.assertEqual(str(self.organization), "SaaS Workspace Alpha")
        self.assertEqual(str(self.user_profile), f"{self.user} (SaaS Workspace Alpha)")
        self.assertEqual(self.user.profile.organization, self.organization)

    def test_company_creation_and_custom_fields(self):
        self.assertEqual(str(self.company), "Acme Corp")
        self.assertEqual(self.company.organization, self.organization)
        self.assertEqual(self.company.custom_fields.get("taille_parc"), 45)
        self.assertEqual(self.company.custom_fields.get("type_licence"), "Enterprise")

    def test_contact_full_name_and_str(self):
        self.assertEqual(self.contact.full_name, "Jean Dupont")
        self.assertEqual(str(self.contact), "Jean Dupont (Acme Corp)")

    def test_contact_email_uniqueness_per_organization(self):
        # 1. Duplication exacte au sein de la MÊME organisation -> doit échouer
        duplicate_contact = Contact(
            organization=self.organization,
            first_name="Autre",
            last_name="Personne",
            email="jean.dupont@acme.com",
        )
        with self.assertRaises(ValidationError):
            duplicate_contact.save()

        # 2. Insensibilité à la casse au sein de la même organisation -> doit échouer
        case_duplicate = Contact(
            organization=self.organization,
            first_name="Autre",
            last_name="Personne",
            email="  JEAN.DUPONT@acme.COM  ",
        )
        with self.assertRaises(ValidationError):
            case_duplicate.save()

        # 3. Même e-mail dans une AUTRE organisation (isolation SaaS) -> doit réussir !
        org_beta = Organization.objects.create(name="Tenant Beta", slug="tenant-beta")
        contact_other_tenant = Contact.objects.create(
            organization=org_beta,
            first_name="Jean",
            last_name="Dupont",
            email="jean.dupont@acme.com",
        )
        self.assertEqual(contact_other_tenant.email, "jean.dupont@acme.com")
        self.assertNotEqual(contact_other_tenant.organization, self.organization)

        # 4. Plusieurs contacts sans email (None ou "") -> autorisés sans collision
        c1 = Contact.objects.create(organization=self.organization, first_name="Sans", last_name="Email1", email="")
        c2 = Contact.objects.create(organization=self.organization, first_name="Sans", last_name="Email2", email=None)
        self.assertIsNone(c1.email)
        self.assertIsNone(c2.email)

    def test_cross_organization_integrity_validation(self):
        org_beta = Organization.objects.create(name="Tenant Beta", slug="tenant-beta")
        company_beta = Company.objects.create(organization=org_beta, name="Beta Corp")
        contact_beta = Contact.objects.create(organization=org_beta, first_name="Paul", last_name="Beta")
        deal_beta = Deal.objects.create(organization=org_beta, title="Deal Beta")

        # Contact: rattaché à une entreprise d'une autre organisation -> rejeté
        invalid_contact = Contact(
            organization=self.organization,
            first_name="Test",
            last_name="Intrus",
            company=company_beta,
        )
        with self.assertRaises(ValidationError) as ctx:
            invalid_contact.save()
        self.assertIn("company", ctx.exception.message_dict)

        # Deal: rattaché à une entreprise ou un contact d'une autre organisation -> rejeté
        invalid_deal = Deal(
            organization=self.organization,
            title="Deal Intrus",
            company=company_beta,
        )
        with self.assertRaises(ValidationError) as ctx:
            invalid_deal.save()
        self.assertIn("company", ctx.exception.message_dict)

        invalid_deal_contact = Deal(
            organization=self.organization,
            title="Deal Intrus 2",
            contact=contact_beta,
        )
        with self.assertRaises(ValidationError) as ctx:
            invalid_deal_contact.save()
        self.assertIn("contact", ctx.exception.message_dict)

        # Interaction: rattachée à un contact d'une autre organisation -> rejeté
        invalid_interaction = Interaction(
            organization=self.organization,
            type=InteractionType.NOTE,
            title="Note cross-tenant",
            contact=contact_beta,
        )
        with self.assertRaises(ValidationError) as ctx:
            invalid_interaction.save()
        self.assertIn("contact", ctx.exception.message_dict)

        # Task: rattachée à un deal d'une autre organisation -> rejeté
        invalid_task = Task(
            organization=self.organization,
            title="Tâche cross-tenant",
            due_date=timezone.now() + timedelta(days=1),
            deal=deal_beta,
        )
        with self.assertRaises(ValidationError) as ctx:
            invalid_task.save()
        self.assertIn("deal", ctx.exception.message_dict)

    def test_protected_deletion_on_contact_and_company(self):
        # Interaction rattachée au contact et à l'entreprise
        Interaction.objects.create(
            organization=self.organization,
            type=InteractionType.NOTE,
            title="Note stratégique",
            contact=self.contact,
            company=self.company,
        )

        with self.assertRaises(models.ProtectedError):
            self.contact.delete()

        with self.assertRaises(models.ProtectedError):
            self.company.delete()

        # Idem pour une tâche
        new_company = Company.objects.create(organization=self.organization, name="Beta SAS")
        new_contact = Contact.objects.create(organization=self.organization, first_name="Paul", last_name="Roc", company=new_company)
        Task.objects.create(
            organization=self.organization,
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
        deal = Deal(
            organization=self.organization,
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

        deal.loss_reason = LossReason.PRICE
        deal.loss_notes = "Budget gelé pour l'année en cours."
        deal.save()
        self.assertEqual(deal.stage, DealStage.LOST)
        self.assertIsNotNone(deal.closed_at)

    def test_deal_weighted_amount_and_pipeline_aggregation(self):
        deal1 = Deal.objects.create(
            organization=self.organization,
            title="Projet Audit",
            amount=Decimal("10000.00"),
            stage=DealStage.PROPOSAL,
            probability=50,
            contact=self.contact,
            company=self.company,
            owner=self.user,
        )
        deal2 = Deal.objects.create(
            organization=self.organization,
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
            organization=self.organization,
            type=InteractionType.CALL,
            title="Appel de qualification sans cible",
        )
        with self.assertRaises(ValidationError):
            interaction.save()

    def test_interaction_updates_last_activity_on_targets(self):
        self.assertIsNone(self.contact.last_activity_at)
        self.assertIsNone(self.company.last_activity_at)

        now = timezone.now()
        Interaction.objects.create(
            organization=self.organization,
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
        now = timezone.now()
        t1 = now - timedelta(hours=2)
        t2 = now

        interactions = [
            Interaction(
                organization=self.organization,
                type=InteractionType.NOTE,
                title="Premier contact importé",
                contact=self.contact,
                company=self.company,
                occurred_at=t1,
            ),
            Interaction(
                organization=self.organization,
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

        self.assertEqual(
            self.contact.last_activity_at.strftime("%Y-%m-%d %H:%M:%S"),
            t2.strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Une ancienne interaction ne doit pas rétrograder la date
        t_old = now - timedelta(days=10)
        old_interaction = [
            Interaction(
                organization=self.organization,
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
            organization=self.organization,
            first_name="Alice",
            last_name="Martin",
            last_activity_at=timezone.now() - timedelta(days=45),
        )
        needs_follow_up_old = Contact.objects.needs_follow_up(days=30)
        self.assertIn(old_contact, needs_follow_up_old)

    def test_task_lifecycle_and_due_date_filters(self):
        past_date = timezone.now() - timedelta(days=1)
        overdue_task = Task.objects.create(
            organization=self.organization,
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

    def test_kanban_view_rendering(self):
        admin_user = User.objects.create_superuser(
            username="adminuser",
            email="admin@example.com",
            password="adminpassword123",
        )
        self.client.force_login(admin_user)

        deal_lead = Deal.objects.create(
            organization=self.organization,
            title="Projet Alpha",
            amount=Decimal("12000.00"),
            stage=DealStage.LEAD,
            contact=self.contact,
            company=self.company,
        )
        deal_won = Deal.objects.create(
            organization=self.organization,
            title="Projet Gagné",
            amount=Decimal("8000.00"),
            stage=DealStage.WON,
            contact=self.contact,
            company=self.company,
            closed_at=timezone.now(),
        )

        response = self.client.get("/admin/core/deal/kanban/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/core/deal/kanban.html")

        # Vérification des colonnes Kanban dans le contexte
        stages_context = response.context["stages"]
        lead_stage = next(s for s in stages_context if s["code"] == DealStage.LEAD)
        won_stage = next(s for s in stages_context if s["code"] == DealStage.WON)

        self.assertIn(deal_lead, lead_stage["deals"])
        self.assertEqual(lead_stage["count"], 1)
        self.assertIn(deal_won, won_stage["deals"])
        self.assertEqual(won_stage["count"], 1)

    def test_kanban_move_stage_view(self):
        admin_user = User.objects.create_superuser(
            username="adminuser2",
            email="admin2@example.com",
            password="adminpassword123",
        )
        self.client.force_login(admin_user)

        deal = Deal.objects.create(
            organization=self.organization,
            title="Opportunité Transition",
            amount=Decimal("15000.00"),
            stage=DealStage.LEAD,
            contact=self.contact,
            company=self.company,
        )

        # 1. Avancer d'étape (LEAD -> QUALIFIED)
        response = self.client.post(
            f"/admin/core/deal/{deal.id}/move-stage/",
            {"action": "advance"},
        )
        self.assertEqual(response.status_code, 302)
        deal.refresh_from_db()
        self.assertEqual(deal.stage, DealStage.QUALIFIED)

        # 2. Marquer directement comme Gagné
        response = self.client.post(
            f"/admin/core/deal/{deal.id}/move-stage/",
            {"action": "win"},
        )
        self.assertEqual(response.status_code, 302)
        deal.refresh_from_db()
        self.assertEqual(deal.stage, DealStage.WON)
        self.assertIsNotNone(deal.closed_at)

    def test_lead_scoring_and_unfold_displays(self):
        from .admin import ContactAdmin
        from django.contrib.admin.sites import AdminSite

        site = AdminSite()
        contact_admin = ContactAdmin(Contact, site)

        # 1. Test du label de scoring visuel
        score_tag, score_text = contact_admin.display_lead_score(self.contact)
        self.assertEqual(score_tag, "HOT")
        self.assertIn("🔥 85 (Chaud)", score_text)

        # 2. Test du statut
        status_code, status_label = contact_admin.display_lead_status(self.contact)
        self.assertEqual(status_code, LeadStatus.QUALIFIED)

        # 3. Test de recalcul du score
        cold_contact = Contact.objects.create(
            organization=self.organization,
            first_name="Bob",
            last_name="Cold",
            lead_score=0,
        )
        cold_tag, cold_text = contact_admin.display_lead_score(cold_contact)
        self.assertEqual(cold_tag, "COLD")
        self.assertIn("❄️ 0 (Froid)", cold_text)

    def test_workflow_rule_validation(self):
        # 1. Règle sur étape sans préciser l'étape cible -> échec
        invalid_rule = WorkflowRule(
            organization=self.organization,
            name="Règle Invalide",
            trigger_event=WorkflowTrigger.DEAL_STAGE_ENTERED,
            trigger_stage="",
        )
        with self.assertRaises(ValidationError) as ctx:
            invalid_rule.save()
        self.assertIn("trigger_stage", ctx.exception.message_dict)

        # 2. Règle valide
        valid_rule = WorkflowRule(
            organization=self.organization,
            name="Relance Devis J+3",
            trigger_event=WorkflowTrigger.DEAL_STAGE_ENTERED,
            trigger_stage=DealStage.PROPOSAL,
            action_type=WorkflowAction.CREATE_TASK,
            task_delay_days=3,
        )
        valid_rule.save()
        self.assertEqual(valid_rule.task_title, "Relance : {deal_title}")

    def test_deal_stage_proposal_workflow_creates_follow_up_task(self):
        # Configuration de la règle : si passage à PROPOSAL -> Tâche de relance à J+3
        WorkflowRule.objects.create(
            organization=self.organization,
            name="Relance devis envoyé",
            trigger_event=WorkflowTrigger.DEAL_STAGE_ENTERED,
            trigger_stage=DealStage.PROPOSAL,
            action_type=WorkflowAction.CREATE_TASK,
            task_title="Rappeler {contact_name} pour le devis {deal_title}",
            task_delay_days=3,
            task_priority=TaskPriority.HIGH,
        )

        deal = Deal.objects.create(
            organization=self.organization,
            title="Prestation Audit Cybersécurité",
            amount=Decimal("18000.00"),
            stage=DealStage.QUALIFIED,
            contact=self.contact,
            company=self.company,
            owner=self.user,
        )

        # Passage à l'étape PROPOSAL (Devis envoyé)
        deal.stage = DealStage.PROPOSAL
        deal.save()

        # Vérification qu'une tâche de relance à J+3 a été créée automatiquement
        tasks = Task.objects.filter(deal=deal)
        self.assertEqual(tasks.count(), 1)
        task = tasks.first()

        self.assertEqual(
            task.title,
            f"Rappeler {self.contact.full_name} pour le devis {deal.title}",
        )
        self.assertEqual(task.priority, TaskPriority.HIGH)
        self.assertEqual(task.assigned_to, self.user)
        self.assertEqual(task.organization, self.organization)

        # Vérifier l'échéance à J+3 (+- 10 secondes)
        expected_due = timezone.now() + timedelta(days=3)
        self.assertEqual(task.due_date.date(), expected_due.date())

    def test_deal_workflow_sends_email_and_logs_interaction(self):
        from django.core import mail

        # Configuration de la règle : Créer une tâche ET envoyer un e-mail automatique
        WorkflowRule.objects.create(
            organization=self.organization,
            name="Accusé devis + Tâche",
            trigger_event=WorkflowTrigger.DEAL_STAGE_ENTERED,
            trigger_stage=DealStage.PROPOSAL,
            action_type=WorkflowAction.CREATE_TASK_AND_EMAIL,
            task_title="Suivi proposition {deal_title}",
            task_delay_days=4,
            email_subject="Votre proposition commerciale : {deal_title}",
            email_body="Bonjour {contact_name},\nVoici les éléments pour {deal_title}.",
        )

        deal = Deal.objects.create(
            organization=self.organization,
            title="Refonte Infrastructure",
            amount=Decimal("25000.00"),
            stage=DealStage.QUALIFIED,
            contact=self.contact,
            company=self.company,
            owner=self.user,
        )

        # Déclenchement de la transition
        deal.stage = DealStage.PROPOSAL
        deal.save()

        # 1. Vérification de l'e-mail envoyé
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertEqual(sent_email.to, [self.contact.email])
        self.assertIn("Refonte Infrastructure", sent_email.subject)
        self.assertIn(self.contact.full_name, sent_email.body)

        # 2. Vérification de l'interaction tracée dans la timeline
        interactions = Interaction.objects.filter(deal=deal, type=InteractionType.EMAIL)
        self.assertEqual(interactions.count(), 1)
        interaction = interactions.first()
        self.assertEqual(interaction.direction, InteractionDirection.OUTBOUND)
        self.assertIn("E-mail automatique", interaction.title)

    def test_inactive_workflow_does_not_trigger(self):
        WorkflowRule.objects.create(
            organization=self.organization,
            name="Règle désactivée",
            trigger_event=WorkflowTrigger.DEAL_STAGE_ENTERED,
            trigger_stage=DealStage.PROPOSAL,
            action_type=WorkflowAction.CREATE_TASK,
            task_title="Ne doit pas être créée",
            is_active=False,
        )

        deal = Deal.objects.create(
            organization=self.organization,
            title="Deal Sans Automatisation",
            stage=DealStage.QUALIFIED,
            contact=self.contact,
            company=self.company,
        )
        deal.stage = DealStage.PROPOSAL
        deal.save()

        self.assertFalse(Task.objects.filter(deal=deal).exists())

    def test_task_admin_displays_and_quick_actions(self):
        from .admin import TaskAdmin
        from django.contrib.admin.sites import AdminSite

        site = AdminSite()
        task_admin = TaskAdmin(Task, site)

        task = Task.objects.create(
            organization=self.organization,
            title="Rappel Devis",
            due_date=timezone.now() - timedelta(days=2),  # en retard
            priority=TaskPriority.URGENT,
            contact=self.contact,
        )

        # 1. Vérification de l'indicateur visuel d'échéance Unfold
        due_status = task_admin.display_due_status(task)
        self.assertIn("🚨 Retard (2j)", due_status)

        # 2. Action Terminer la tâche
        task.mark_completed()
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        completed_status = task_admin.display_due_status(task)
        self.assertIn("✅ Fait", completed_status)

    def test_task_mark_completed_logs_interaction(self):
        task = Task.objects.create(
            organization=self.organization,
            title="Appel de cadrage client",
            description="Vérifier les besoins d'intégration API",
            due_date=timezone.now(),
            contact=self.contact,
            assigned_to=self.user,
        )
        task.mark_completed(log_interaction=True)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.COMPLETED)

        # Vérification qu'une interaction a été consignée dans la timeline
        interaction = Interaction.objects.filter(contact=self.contact, title__contains="Appel de cadrage").first()
        self.assertIsNotNone(interaction)
        self.assertIn("Réalisée le", interaction.content)
        self.assertEqual(interaction.user, self.user)

    def test_contact_workflow_on_qualification(self):
        from django.core import mail

        WorkflowRule.objects.create(
            organization=self.organization,
            name="Workflow Nouveau Lead Qualifié",
            trigger_event=WorkflowTrigger.CONTACT_QUALIFIED,
            action_type=WorkflowAction.CREATE_TASK_AND_EMAIL,
            task_title="Démo produit pour {contact_name}",
            task_delay_days=2,
            email_subject="Bienvenue chez nous {contact_name}",
            email_body="Bonjour {contact_name},\nNous sommes ravis d'échanger avec vous.",
        )

        contact = Contact.objects.create(
            organization=self.organization,
            first_name="Sophie",
            last_name="Martin",
            email="sophie.martin@example.com",
            lead_status=LeadStatus.NEW,
            owner=self.user,
        )

        # Qualification du prospect
        contact.lead_status = LeadStatus.QUALIFIED
        contact.save()

        # 1. Vérification de la tâche créée à J+2
        task = Task.objects.filter(contact=contact).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.title, "Démo produit pour Sophie Martin")
        expected_due = timezone.now() + timedelta(days=2)
        self.assertEqual(task.due_date.date(), expected_due.date())

        # 2. Vérification de l'e-mail envoyé
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Sophie Martin", mail.outbox[0].subject)

        # 3. Vérification de l'interaction tracée
        interaction = Interaction.objects.filter(contact=contact, type=InteractionType.EMAIL).first()
        self.assertIsNotNone(interaction)
        self.assertIn("E-mail automatique", interaction.title)

    def test_anti_enumeration_uuid_generation(self):
        import uuid
        # Vérification de la présence d'un UUID unique non-nul sur chaque modèle
        self.assertIsInstance(self.organization.uuid, uuid.UUID)
        self.assertIsInstance(self.company.uuid, uuid.UUID)
        self.assertIsInstance(self.contact.uuid, uuid.UUID)

        deal = Deal.objects.create(
            organization=self.organization,
            title="UUID Test Deal",
            contact=self.contact,
        )
        self.assertIsInstance(deal.uuid, uuid.UUID)
        self.assertNotEqual(self.organization.uuid, deal.uuid)

    def test_soft_delete_and_restore_cycle(self):
        company = Company.objects.create(
            organization=self.organization,
            name="Société Test Corbeille",
        )
        self.assertFalse(company.is_deleted)
        self.assertIsNone(company.deleted_at)

        # 1. Mise à la corbeille (Soft delete)
        company.soft_delete()
        company.refresh_from_db()
        self.assertTrue(company.is_deleted)
        self.assertIsNotNone(company.deleted_at)

        # Vérifier que .alive() l'exclut et .deleted() la récupère
        self.assertFalse(Company.objects.alive().filter(pk=company.pk).exists())
        self.assertTrue(Company.objects.deleted().filter(pk=company.pk).exists())

        # 2. Restauration depuis la corbeille
        company.restore()
        company.refresh_from_db()
        self.assertFalse(company.is_deleted)
        self.assertIsNone(company.deleted_at)
        self.assertTrue(Company.objects.alive().filter(pk=company.pk).exists())

    def test_organization_memberships_multi_workspace(self):
        from django.db import IntegrityError

        org_beta = Organization.objects.create(
            name="SaaS Workspace Beta",
            slug="workspace-beta",
        )

        # Adhésion de l'utilisateur à l'espace Alpha (Propriétaire)
        mem_alpha = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=OrganizationRole.OWNER,
            is_default=True,
        )
        self.assertEqual(mem_alpha.role, OrganizationRole.OWNER)
        self.assertTrue(mem_alpha.is_default)

        # Adhésion du même utilisateur à l'espace Beta (Collaborateur)
        mem_beta = OrganizationMembership.objects.create(
            organization=org_beta,
            user=self.user,
            role=OrganizationRole.MEMBER,
            is_default=False,
        )
        self.assertEqual(mem_beta.organization, org_beta)

        # Vérifier les adhésions multiples
        user_memberships = self.user.memberships.all()
        self.assertEqual(user_memberships.count(), 2)

        # Vérifier la contrainte d'unicité (user, organization)
        with self.assertRaises(IntegrityError):
            OrganizationMembership.objects.create(
                organization=self.organization,
                user=self.user,
                role=OrganizationRole.VIEWER,
            )

    def test_crm_kpi_summary_reporting(self):
        from .reporting import get_crm_kpi_summary

        # Création de deals de test
        deal_active = Deal.objects.create(
            organization=self.organization,
            title="Deal En Négociation",
            amount=Decimal("10000.00"),
            stage=DealStage.NEGOTIATION,
            probability=50,
            contact=self.contact,
            owner=self.user,
        )
        deal_won = Deal.objects.create(
            organization=self.organization,
            title="Deal Signé",
            amount=Decimal("20000.00"),
            stage=DealStage.WON,
            probability=100,
            contact=self.contact,
            owner=self.user,
            closed_at=timezone.now(),
        )
        deal_lost = Deal.objects.create(
            organization=self.organization,
            title="Deal Abandonné Prix",
            amount=Decimal("5000.00"),
            stage=DealStage.LOST,
            loss_reason=LossReason.PRICE,
            contact=self.contact,
            owner=self.user,
            closed_at=timezone.now(),
        )

        # Création de tâches de test
        Task.objects.create(
            organization=self.organization,
            title="Tâche En Retard",
            due_date=timezone.now() - timedelta(days=3),
            status=TaskStatus.PENDING,
            contact=self.contact,
            assigned_to=self.user,
        )
        Task.objects.create(
            organization=self.organization,
            title="Tâche Du Jour",
            due_date=timezone.now(),
            status=TaskStatus.PENDING,
            contact=self.contact,
            assigned_to=self.user,
        )

        # Calcul des KPIs
        kpis = get_crm_kpi_summary(organization_id=self.organization.id, days=30)

        # Vérifications financières
        self.assertEqual(kpis["weighted_pipeline"], Decimal("5000.00"))
        self.assertEqual(kpis["total_active_pipeline"], Decimal("10000.00"))
        self.assertEqual(kpis["won_revenue_period"], Decimal("20000.00"))
        self.assertEqual(kpis["won_deals_count_period"], 1)

        # Vérifications taux de conversion (1 Won / (1 Won + 1 Lost) = 50%)
        self.assertEqual(kpis["win_rate"], 50.0)

        # Vérifications motifs d'abandon
        self.assertTrue(any(r["code"] == LossReason.PRICE for r in kpis["loss_reasons_summary"]))

        # Vérifications des urgences ("Qui relancer aujourd'hui")
        self.assertGreaterEqual(kpis["overdue_tasks_count"], 1)
        self.assertGreaterEqual(kpis["today_tasks_count"], 1)
        self.assertTrue(len(kpis["top_urgent_tasks"]) > 0)

    def test_contact_segmentation_querysets(self):
        # 1. Contact inactif (> 30j)
        old_time = timezone.now() - timedelta(days=45)
        inactive_contact = Contact.objects.create(
            organization=self.organization,
            first_name="Marc",
            last_name="Inactif",
            lead_status=LeadStatus.CONTACTED,
            last_activity_at=old_time,
        )

        # 2. Contact chaud non converti (score >= 70 et pas client)
        hot_contact = Contact.objects.create(
            organization=self.organization,
            first_name="Claire",
            last_name="Chaude",
            lead_status=LeadStatus.QUALIFIED,
            lead_score=80,
        )

        # 3. Client dormant (> 60j)
        dormant_client = Contact.objects.create(
            organization=self.organization,
            first_name="Pierre",
            last_name="Dormant",
            lead_status=LeadStatus.CUSTOMER,
            last_activity_at=timezone.now() - timedelta(days=70),
        )

        # Vérification des QuerySets de segmentation
        inactive_list = Contact.objects.filter(organization=self.organization).inactive_for(days=30)
        self.assertIn(inactive_contact, inactive_list)

        hot_list = Contact.objects.filter(organization=self.organization).hot_unconverted()
        self.assertIn(hot_contact, hot_list)
        self.assertNotIn(dormant_client, hot_list)

        dormant_list = Contact.objects.filter(organization=self.organization).dormant_customers(days=60)
        self.assertIn(dormant_client, dormant_list)

        no_task_list = Contact.objects.filter(organization=self.organization).without_pending_tasks()
        self.assertIn(inactive_contact, no_task_list)

    def test_deal_segmentation_querysets(self):
        now = timezone.now().date()
        closing_deal = Deal.objects.create(
            organization=self.organization,
            title="Deal Fin de Mois",
            amount=Decimal("15000.00"),
            stage=DealStage.PROPOSAL,
            expected_close_date=now + timedelta(days=10),
            contact=self.contact,
        )
        stalled_deal = Deal.objects.create(
            organization=self.organization,
            title="Deal Bloqué",
            amount=Decimal("8000.00"),
            stage=DealStage.LEAD,
            last_activity_at=timezone.now() - timedelta(days=20),
            contact=self.contact,
        )

        closing_qs = Deal.objects.filter(organization=self.organization).closing_in_period(days=30)
        self.assertIn(closing_deal, closing_qs)

        stalled_qs = Deal.objects.filter(organization=self.organization).stalled(days=15)
        self.assertIn(stalled_deal, stalled_qs)

    def test_admin_batch_schedule_followup_action(self):
        from .admin import ContactAdmin
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        site = AdminSite()
        contact_admin = ContactAdmin(Contact, site)
        rf = RequestFactory()
        request = rf.post("/admin/core/contact/")
        request.user = self.user
        setattr(request, "session", {})
        setattr(request, "_messages", FallbackStorage(request))

        contacts = Contact.objects.filter(id=self.contact.id)
        contact_admin.batch_schedule_followup_action(request, contacts)

        # Vérification qu'une tâche de relance à J+2 a été créée pour le contact
        created_task = Task.objects.filter(contact=self.contact, title__contains="Relance ciblée").first()
        self.assertIsNotNone(created_task)
        self.assertEqual(created_task.organization, self.organization)
        self.assertEqual(created_task.priority, TaskPriority.HIGH)  # Car lead_score >= 70

    def test_admin_export_csv_actions(self):
        from .admin import ContactAdmin, DealAdmin
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        site = AdminSite()
        rf = RequestFactory()
        request = rf.get("/admin/")
        request.user = self.user

        # 1. Export CSV Contacts
        contact_admin = ContactAdmin(Contact, site)
        resp_contact = contact_admin.export_segment_csv_action(request, Contact.objects.filter(id=self.contact.id))
        self.assertEqual(resp_contact.status_code, 200)
        self.assertEqual(resp_contact["Content-Type"], "text/csv; charset=utf-8-sig")
        content_contact = resp_contact.content.decode("utf-8-sig")
        self.assertIn("Jean Dupont", content_contact)
        self.assertIn("jean.dupont@acme.com", content_contact)

        # 2. Export CSV Deals
        deal = Deal.objects.create(
            organization=self.organization,
            title="Contrat Cloud 2026",
            amount=Decimal("12000.00"),
            stage=DealStage.PROPOSAL,
            contact=self.contact,
        )
        deal_admin = DealAdmin(Deal, site)
        resp_deal = deal_admin.export_pipeline_csv_action(request, Deal.objects.filter(id=deal.id))
        self.assertEqual(resp_deal.status_code, 200)
        self.assertEqual(resp_deal["Content-Type"], "text/csv; charset=utf-8-sig")
        content_deal = resp_deal.content.decode("utf-8-sig")
        self.assertIn("Contrat Cloud 2026", content_deal)
        self.assertIn("12000.00", content_deal)

    def test_unfold_dashboard_renders_with_kpi_metrics(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()

        self.client.force_login(self.user)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        self.assertIn("Tableau de Bord Commercial", content)
        self.assertIn("CA Prévisionnel (Pondéré)", content)
        self.assertIn("Pipeline Commercial Brut", content)
        self.assertIn("Entonnoir de Conversion Commercial", content)
        self.assertIn("Segmentation Avancée & Actions Ciblées", content)
        self.assertIn("Qui relancer aujourd'hui", content)
