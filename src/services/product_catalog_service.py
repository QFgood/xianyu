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
