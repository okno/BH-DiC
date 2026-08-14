from bh_dic.dic.service import DicService as CompatibilityDicService
from bh_dic.services.dic_service import DicService


def test_mandatory_dic_service_module_reexports_the_single_implementation() -> None:
    assert CompatibilityDicService is DicService
