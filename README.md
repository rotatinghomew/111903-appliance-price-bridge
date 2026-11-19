# 家电换代价格指数与合同调价复核

这是用于商品换代价格与采购合同资料分析的本地工作区。样例资料为合成数据，不代表真实市场观察或结算记录。工作区包含 Python 3.11+ 命令入口和 JSON 记录，可离线查看与核验。

## 运行

无需安装第三方依赖。设置 `PYTHONPATH=src` 后运行 `python3 -m appliance-price-bridge` 可查看工作区版本。运行现有基线检查：`PYTHONPATH=src python3 -m unittest discover -s tests -v`。

## 资料

`data/sample.json` 保存小型合成记录，顶层键分别为 products, observations, bridges, contracts。
