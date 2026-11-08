"""Local verified catalog adapter; offline operation does not depend on an external API."""
from copy import deepcopy
import re
from src.domain.catalog.mac_mini import MODELS, REVISION
from src.domain.models.market_target import normalize


def search_products(query=''):
    text = normalize(query).replace('macmini', 'mac mini')
    text = re.sub(r'(?<=mini)(?=m\d)', ' ', text)
    text = re.sub(r'(m\d+)(pro|max|ultra)', r'\1 \2', text)
    tokens = text.split()
    result = []
    for model in MODELS:
        searchable = normalize(f"{model['name']} {model['chip']} {model['year'] or ''} Apple 苹果 Mac mini 迷你主机")
        if all(token in searchable for token in tokens):
            result.append(deepcopy(model))
    return {'revision': REVISION, 'items': result}


def catalog_search_fields(payload):
    """A selected model determines the platform query; free text cannot override it."""
    if payload.get('monitor_mode', 'market') != 'market' or not payload.get('product_selection'):
        return {}
    from src.domain.models.product_selection import ProductSelection, find_product
    selected = ProductSelection.model_validate(payload['product_selection'])
    product = find_product(selected.catalog_id)
    return {'keyword': product['name'], 'decision_mode': 'ai', 'keyword_rules': []}
