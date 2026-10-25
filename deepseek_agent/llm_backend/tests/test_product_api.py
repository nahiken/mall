"""P2-04 期间补的回归：`get_category_tree` 此前根本没实现。

发现经过：拆分 service.py 后写 `_get_category_tree_cached` 的单测，monkeypatch
`pa.get_category_tree` 时抛 AttributeError —— 说明检索侧一直在调一个不存在的函数，
被 `except Exception` 静默降级成空树，品类树增强从来没生效过。

这组用例钉住三件事：成功解析 data 列表、非 200 降级为空、脏 payload 降级为空。
"""
import pytest

from mall_agent import product_api as pa


class _Resp:
    def __init__(self, status_code=200, payload=None, exc=None):
        self.status_code = status_code
        self._payload = payload
        self._exc = exc

    def json(self):
        if self._exc is not None:
            raise self._exc
        return self._payload


class _Client:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.is_closed = False   # product_api.get_client() 会读这个属性判断是否重建
        self.urls: list[str] = []

    async def get(self, url, **kwargs):
        self.urls.append(url)
        if self._exc is not None:
            raise self._exc
        return self._resp


@pytest.fixture
def fake_client(monkeypatch):
    def install(resp=None, exc=None):
        client = _Client(resp, exc)
        monkeypatch.setattr(pa, "_client", client)
        return client

    return install


async def test_category_tree_returns_data_list(fake_client):
    client = fake_client(_Resp(payload={"code": 200, "data": [
        {"id": 1, "name": "手机", "children": [{"id": 2, "name": "5G手机"}]},
    ]}))
    tree = await pa.get_category_tree()
    assert [node["name"] for node in tree] == ["手机"]
    assert client.urls[0].endswith("/product/categoryTreeList")


async def test_category_tree_filters_non_dict_nodes(fake_client):
    fake_client(_Resp(payload={"data": [{"name": "手机"}, "junk", None, 42]}))
    tree = await pa.get_category_tree()
    assert tree == [{"name": "手机"}]


@pytest.mark.parametrize("status", [401, 404, 500, 503])
async def test_category_tree_non_200_degrades_to_empty(fake_client, status):
    fake_client(_Resp(status_code=status, payload={"data": [{"name": "手机"}]}))
    assert await pa.get_category_tree() == []


async def test_category_tree_transport_error_degrades(fake_client):
    fake_client(exc=RuntimeError("connection refused"))
    assert await pa.get_category_tree() == []


async def test_category_tree_bad_payload_degrades(fake_client):
    fake_client(_Resp(exc=ValueError("not json")))
    assert await pa.get_category_tree() == []


async def test_category_tree_missing_data_key_degrades(fake_client):
    fake_client(_Resp(payload={"code": 200}))
    assert await pa.get_category_tree() == []


async def test_category_tree_data_not_a_list_degrades(fake_client):
    fake_client(_Resp(payload={"data": {"a": 1}}))
    assert await pa.get_category_tree() == []
