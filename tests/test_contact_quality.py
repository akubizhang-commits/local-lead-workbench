"""Pure historical cleanup policy; no store or model dependency."""
import unittest

from leadworkbench.contact_quality import (
    is_person_name, is_job_title, is_procurement_role, invalid_contact_reason,
)


class QualityTests(unittest.TestCase):
    def test_plausible_names_include_published_single_token_names(self):
        for value in ['Alice Example', 'FIRST EXAMPLE', 'First', 'José García', "Anne-Marie O’Neill", '张伟']:
            self.assertTrue(is_person_name(value), value)
        for value in [None, '', 'Quality Control', 'Buyer protection', 'Customer Service',
                      'Why Buyers Use Fashion Atlas Instead Of Searching Alone',
                      'Plan Smaller Production Runs', 'Get in Touch', 'alice@example.org']:
            self.assertFalse(is_person_name(value), value)

    def test_titles_are_roles_not_sentences(self):
        for value in ['Buyer', 'Senior Buyer', 'Purchasing Director', 'Head of Sourcing',
                      'Former Purchasing Director', 'Sales Manager', '采购经理', 'Supply Chain Manager']:
            self.assertTrue(is_job_title(value), value)
        for value in ['Buyer protection', 'Plan smaller production runs with realistic sourcing options',
                      'Why Buyers Use Fashion Atlas Instead Of Searching Alone', 'Email our Sales Manager',
                      'Sourcing solutions', None]:
            self.assertFalse(is_job_title(value), value)
        self.assertTrue(is_procurement_role('Head of Sourcing'))
        self.assertTrue(is_procurement_role('Procurement Department'))
        self.assertFalse(is_procurement_role('Sales Manager'))
        self.assertFalse(is_procurement_role('Buyer protection'))

    def test_historical_cleanup_flags_bad_fields_without_archiving_valid_channels(self):
        for contact in [
            {'name': 'Quality Control', 'role': 'Buyer', 'kind': 'procurement'},
            {'name': 'Alice Example', 'role': 'Buyer protection', 'kind': 'procurement'},
            {'name': 'Alice Example', 'email': 'info@example.org', 'role': 'Buyer'},
            {'name': None, 'role': 'Plan smaller production runs with realistic sourcing options', 'kind': 'procurement'},
            {'kind': 'supplier_form', 'url': 'https://example.org/compare', 'evidence': 'Compare suppliers'},
        ]:
            self.assertIsInstance(invalid_contact_reason(contact), str, contact)
        for contact in [
            {'name': 'Alice Example', 'role': 'Buyer', 'kind': 'procurement', 'email': None},
            {'name': None, 'email': 'info@example.org', 'kind': 'general'},
            {'name': None, 'role': 'Procurement Department', 'email': 'desk@example.org', 'kind': 'procurement', 'role_status': 'department'},
            {'name': None, 'role': 'Customer Service Department', 'email': 'desk@example.org', 'kind': 'general', 'role_status': 'department'},
            {'kind': 'supplier_form', 'url': 'https://example.org/apply', 'evidence': 'Supplier application'},
            {'kind': 'contact_form', 'url': 'https://example.org/contact', 'evidence': 'Contact us'},
            {'name': 'Alice Example', 'role': 'Former Purchasing Director', 'role_status': 'historical'},
        ]:
            self.assertIsNone(invalid_contact_reason(contact), contact)


class CleanupCompatibilityTests(unittest.TestCase):
    def test_ambiguous_legacy_fields_are_not_automatically_archived(self):
        for record in [
            {'name': 'Alex', 'role': 'Sales', 'kind': 'general'},
            {'name': 'Élodie Martin', 'role': 'Textile Technologist'},
            {'name': 'alice smith', 'role': 'Responsable des collections'},
            {'name': 'Prince'},
            {'kind': 'supplier_form', 'url': 'https://example.org/portal', 'evidence': 'Vendor portal'},
        ]:
            before = dict(record)
            self.assertIsNone(invalid_contact_reason(record))
            self.assertEqual(record, before)
        self.assertTrue(is_person_name('Alex'))
        self.assertTrue(is_job_title('Sales'))


class ConservativeNameCleanupTests(unittest.TestCase):
    def test_occupational_surname_alone_is_not_demonstrably_invalid(self):
        self.assertIsNone(invalid_contact_reason({'name': 'Alice Head', 'role': 'Buyer'}))
        self.assertIsNone(invalid_contact_reason({'name': 'Alex Sales', 'email': 'alex@example.org'}))


def test_service_headings_and_keyword_tags_are_not_people():
 from leadworkbench.contact_quality import invalid_contact_reason,is_person_name
 for name in ['Resource Guidance','Production Management','Product Development']:
  assert not is_person_name(name)
  assert invalid_contact_reason({'name':name,'role':'Fabric & Trim Sourcing','kind':'procurement'})=='invalid_person_name'
 assert invalid_contact_reason({'name':'Pauline Lock','role':'Production Manager','kind':'general'}) is None
 assert invalid_contact_reason({'name':'Alice Brown','role':'Tags: Resource, Sourcing','kind':'procurement'})=='invalid_job_title'


def test_audit_cookie_prose_mixed_identity_and_fake_supplier():
 from leadworkbench.contact_quality import invalid_contact_reason
 for name,role in [('This website uses cookies.','New York Apparel Sourcing LLC'),('We make and fit everything in our NYC factory, then manufacture overseas.','Fabric Sourcing'),('Beth Bosch - EVP & COO -','Purchasing Coordinator'),('Beautiful Connection Group','Buyer Join')]:
  assert invalid_contact_reason({'name':name,'role':role,'kind':'procurement'})
 assert invalid_contact_reason({'kind':'supplier_form','evidence':'Top 7 wholesale vendors','url':'https://example.com/blog/article'})=='supplier_link_without_contact_intent'
 assert invalid_contact_reason({'kind':'supplier_form','evidence':'Vendor Registration','url':'https://example.com/vendor-registration'}) is None
 assert invalid_contact_reason({'name':'ALEX BUYER','role':'Buyer','email':'alex@brand.example.com','kind':'procurement'}) is None


def test_technical_vendor_mail_not_customer_contact():
 from leadworkbench.contact_quality import invalid_contact_reason
 assert invalid_contact_reason({'email':'filler@godaddy.com','source_url':'https://brand.example.com'})=='placeholder_email'
 assert invalid_contact_reason({'email':'support@starapps.studio','source_url':'https://brand.example.com'})=='third_party_service_email'
 assert invalid_contact_reason({'email':'support@starapps.studio','source_url':'https://starapps.studio/contact'}) is None
 assert invalid_contact_reason({'email':'person@gmail.example.org','source_url':'https://brand.example.com'}) is None


def test_cleanup_does_not_recreate_placeholder_as_company_channel(tmp_path):
 from leadworkbench.db import Store
 s=Store(tmp_path);c=s.manual_company('Brand','https://brand.example')
 s.apply_enrichment(c['id'],{'status':'completed','contacts':[{'email':'filler@godaddy.com','kind':'general','source_url':'https://brand.example','evidence':'filler@godaddy.com'}]})
 with s.connect() as con:
  assert con.execute("SELECT count(*) FROM contacts WHERE email='filler@godaddy.com' AND review_status='auto_recorded'").fetchone()[0]==0
