"""Conservative contact-crawl link classification, without network access."""
import re
from urllib.parse import unquote, urlsplit

CONTACT = re.compile(r'\b(contact|about|team|staff|office|buyer|buyers|purchasing|procurement|sourcing|supplier|suppliers|vendor|vendors|application)\b|联系|采购|供应商', re.I)
GARMENTS = {'romper','rompers','jumpsuit','jumpsuits','dress','dresses','top','tops','bottom','bottoms',
            'skirt','skirts','jeans','denim','pants','shorts','outerwear','jacket','jackets','sweater','sweaters',
            'blouse','blouses','leggings','activewear','swimwear','bodysuit','bodysuits','tshirt','tshirts'}
MODIFIERS = {'plus','size','sizes','women','womens','men','mens','kids','all','new','sale','mini','maxi','midi',
             'fall','winter','spring','summer','and','sets','set','clothing','apparel','casual','basic','basics'}
ASSET_SUFFIXES = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico', '.avif',
                  '.atom', '.rss', '.js', '.css', '.woff', '.woff2', '.ttf', '.eot',
                  '.zip', '.gz', '.mp3', '.mp4', '.webm')
DOCUMENT_SUFFIXES = ('.pdf', '.doc', '.docx')

# Pages whose purpose is reaching the company: contact, inquiry, quote,
# supplier cooperation and dealer/distributor applications. Narrower than
# CONTACT (no about/team/staff), because only these pages make a form an entry.
CONTACT_PAGE = re.compile(
    r'\b(contact|contacts|contactus|get in touch|enquiry|enquiries|enquire|inquiry|inquiries|inquire|'
    r'quote|quotes|quotation|rfq|supplier|suppliers|vendor|vendors|dealer|dealers|dealership|reseller|'
    r'resellers|become a distributor|distributor application|distributorship|wholesale application|'
    r'trade account|trade application)\b|'
    r'联系|聯絡|聯繫|询价|詢價|报价|報價|供应商|供應商|经销|經銷|代理申请|代理申請', re.I)
# Titles usually repeat the company name ("ABC Metal Suppliers"), so a title
# needs an explicit call to contact, ask for a quote or apply.
CONTACT_TITLE = re.compile(
    r'\b(contact|get in touch|enquiry|enquiries|inquiry|inquiries|request (?:a )?quote|quote request|'
    r'get a quote|rfq|become an? (?:supplier|vendor|dealer|distributor|reseller)|'
    r'(?:supplier|vendor|dealer|distributor|reseller|wholesale) (?:application|registration|inquiry|enquiry))\b|'
    r'联系我们|聯絡我們|联系方式|聯繫方式|询价|詢價|报价|報價|供应商合作|供應商合作|经销申请|經銷申請|代理申请|代理申請', re.I)


NON_CONTACT_SECTIONS = {'blog', 'blogs', 'news', 'article', 'articles', 'post', 'posts', 'press', 'privacy',
                        'privacy-policy', 'policy', 'policies', 'terms', 'legal', 'careers', 'career', 'jobs',
                        'job', 'product', 'products', 'shop', 'store', 'category', 'categories', 'collections',
                        'tag', 'tags', 'author'}


def contact_page(url, title=''):
    """True when the URL path or page title names a contact-type page.

    Blog posts, policies, careers and catalogue pages never count, even when a
    slug or article title mentions suppliers or quotes. Only the last path
    segment names the page itself.
    """
    segments = [s for s in unquote(urlsplit(url).path).casefold().split('/') if s]
    if any(s in NON_CONTACT_SECTIONS for s in segments):
        return False
    slug = re.sub(r'[-_.]+', ' ', segments[-1]) if segments else ''
    return bool(CONTACT_PAGE.search(slug) or CONTACT_TITLE.search(title or ''))


def garment_category(text):
    words = set(re.findall(r'[a-z]+', text.casefold()))
    return bool(words & GARMENTS) and words <= GARMENTS | MODIFIERS

def link_kind(url, label='', contexts=()):
    path = unquote(urlsplit(url).path).casefold().rstrip('/')
    if path.endswith(DOCUMENT_SUFFIXES):
        return 'document'
    # Published images and feeds cannot yield HTML contacts. Check before
    # contact intent: a "contact" label does not turn a team photo into a form.
    if path.endswith(ASSET_SUFFIXES):
        return 'asset'
    slug = path.rsplit('/', 1)[-1]
    # A real contact path wins over apparel words, but a generic supplier label
    # must not promote an explicit product SKU into the contact queue.
    path_intent = CONTACT.search(re.sub(r'[-_/]', ' ', path))
    if path_intent:
        return 'page'
    if re.match(r'^style[-_]\d{3,}(?:[-_]|$)', slug):
        return 'product'
    if CONTACT.search(label):
        return 'page'
    if garment_category(slug):
        return 'category'
    catalog_context = any(re.search(r'\b(product|catalog|category|categories|shop)\b',
                         re.sub(r'[-_]', ' ', text), re.I) for text in contexts)
    if catalog_context and garment_category(label):
        return 'category'
    return 'page'
