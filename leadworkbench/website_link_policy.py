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
