"""A user-selected identity, independent from a task name or free-form Prompt."""
from pydantic import BaseModel, ConfigDict, StrictInt, model_validator
from src.domain.catalog.mac_mini import MODELS


def find_product(catalog_id):
    return next((model for model in MODELS if model['id'] == catalog_id), None)


class ProductSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    catalog_id: str
    memory_gb: StrictInt
    storage_gb: StrictInt

    @model_validator(mode='after')
    def supported_configuration(self):
        model = find_product(self.catalog_id)
        if model is None:
            raise ValueError('商品库中没有该型号，请重新搜索并选择')
        if self.memory_gb not in model['memory_options']:
            raise ValueError('该型号不支持所选内存配置')
        if self.storage_gb not in model['storage_options']:
            raise ValueError('该型号不支持所选内置SSD配置')
        return self

    def label(self):
        model = find_product(self.catalog_id)
        return f"{model['name']} / {self.memory_gb}GB / {self.storage_gb}GB内置SSD"
