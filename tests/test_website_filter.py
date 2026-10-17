from unittest.mock import patch
import pytest
from leadworkbench.sources import website as w
from test_website import Response, Session, website_clock

pytestmark = pytest.mark.usefixtures("website_clock")

@pytest.mark.asyncio
async def test_catalog_and_documents_do_not_turn_contact_scan_into_failure():
    root='https://example.org/'
    html='''<nav class="categories"><a href="/plus-rompers-jumpsuits">Plus Rompers &amp; Jumpsuits</a>
    <a href="/style-77844---mesh-top">Mesh Top</a><a href="/seasonal-edit">Dresses</a>
    <a href="/vendor-application">Vendor application</a></nav>
    <a href="/forms/ccform.pdf">Credit card form</a><a href="/contact">Contact us</a>'''
    session=Session({root+'robots.txt':Response(status=404),root:Response(html),
                    root+'contact':Response('contact@example.org'),root+'vendor-application':Response('vendors@example.org')})
    with patch.object(w.aiohttp,'ClientSession',return_value=session):
        result=await w.enrich_website(root)
    assert result['status']=='completed'
    assert result['summary']['pages_skipped']==3
    assert result['summary']['files_unparsed']==1
    assert result['checkpoint']['files_unparsed'][0]['url']==root+'forms/ccform.pdf'
    assert len(session.requested)==4


def test_contact_and_uncertain_urls_are_not_filtered_by_product_context():
    html='''<nav class="product-menu"><a href="/contact">Contact</a><a href="/our-team">Our team</a>
    <a href="/apparel-sourcing">Apparel sourcing</a><a href="/supplier-forms.pdf">Supplier application</a>
    <a href="/new-york-office">New York office</a></nav>'''
    result=w.parse_page(html,'https://example.org/')
    urls={u for u,_ in result['links']}
    assert {'https://example.org/contact','https://example.org/our-team','https://example.org/apparel-sourcing',
            'https://example.org/new-york-office'} <= urls
    assert not result.get('skipped_links')
    assert result['files_unparsed'][0]['url'].endswith('supplier-forms.pdf')
    assert not any(c.get('url','').endswith('.pdf') for c in result['contacts'])


@pytest.mark.asyncio
async def test_extensionless_pdf_is_recorded_as_document():
    root='https://example.org/'
    session=Session({root+'robots.txt':Response(status=404),root:Response('<a href="/download">Download</a>'),
                    root+'download':Response(headers={'Content-Type':'application/pdf'})})
    with patch.object(w.aiohttp,'ClientSession',return_value=session):
        result=await w.enrich_website(root)
    assert result['status']=='completed'
    assert result['summary']['files_unparsed']==1
    assert result['summary']['failed']==0

@pytest.mark.asyncio
async def test_old_checkpoint_filters_pending_catalog_without_network():
    root='https://example.org/'
    urls=[root+'plus-rompers-jumpsuits',root+'style-77844---long-slv-button-down-mesh-top-&-cami-set',root+'forms/ccform.pdf']
    checkpoint={'version':1,'start':root,'queue':{u:[1,n,1] for n,u in enumerate(urls)},
                'summary':{'pages_checked':6},'fetched':[root],'batches':4}
    session=Session({})
    with patch.object(w.aiohttp,'ClientSession',return_value=session):
        result=await w.enrich_website(root,checkpoint=checkpoint)
    assert session.requested==[]
    assert result['status']=='completed' and not result['has_more']
    assert result['summary']['pages_checked']==6
    assert result['summary']['pages_skipped']==2 and result['summary']['files_unparsed']==1

def test_team_path_is_not_mistaken_for_product_category():
    from leadworkbench.website_link_policy import link_kind
    assert link_kind('https://example.org/team/denim','Denim')=='page'


@pytest.mark.asyncio
async def test_document_as_website_does_not_claim_html_scan_completed():
    with patch.object(w.aiohttp,'ClientSession',return_value=Session({})):
        result=await w.enrich_website('https://example.org/company.pdf')
    assert result['status']=='blocked'
    assert result['summary']['files_unparsed']==1
