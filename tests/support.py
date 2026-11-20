"""测试辅助：统一从 ``data/`` 读取样例分析。"""

import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from appliance_price_bridge import analyse  # noqa: E402


def season(data_dir=None):
    return analyse(data_dir or ROOT / "data")


def from_payload(season_payload, chain_payload, tmp_path):
    """把改写过的资料写成临时目录，返回新的 Analysis。"""
    import json

    (tmp_path / "overlap-season.json").write_text(
        json.dumps(season_payload, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "publication_chain.json").write_text(
        json.dumps(chain_payload, ensure_ascii=False), encoding="utf-8"
    )
    return analyse(tmp_path)


class Scenario:
    """在样例副本上做最小改动，构造规则边界场景。"""

    def __init__(self):
        import copy
        import json

        self.season = copy.deepcopy(
            json.loads((ROOT / "data" / "overlap-season.json").read_text(encoding="utf-8"))
        )
        self.chain = copy.deepcopy(
            json.loads((ROOT / "data" / "publication_chain.json").read_text(encoding="utf-8"))
        )

    def build(self):
        import tempfile

        directory = tempfile.mkdtemp()
        return from_payload(self.season, self.chain, Path(directory))

    def contract(self, contract_id):
        return next(row for row in self.season["contracts"] if row["id"] == contract_id)

    def edition(self, edition_id):
        return next(row for row in self.chain["editions"] if row["id"] == edition_id)
