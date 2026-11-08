from fastapi import APIRouter, Query
from src.services.product_catalog_service import search_products

router = APIRouter(prefix='/api/catalog', tags=['catalog'])


@router.get('/products')
async def products(q: str = Query('', max_length=100)):
    return search_products(q)
