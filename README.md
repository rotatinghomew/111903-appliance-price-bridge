# 家电换代价格指数与合同调价复核

这是用于商品换代价格与采购合同资料分析的本地工作区。样例资料为合成数据，不代表真实市场观察或结算记录。工作区包含 Python 3.11+ 命令入口和 JSON 记录，可离线查看与核验。

## 运行

无需安装第三方依赖。设置 `PYTHONPATH=src` 后运行 `python3 -m appliance-price-bridge` 可查看工作区版本。运行现有基线检查：`PYTHONPATH=src python3 -m unittest discover -s tests -v`。

## 资料

`data/sample.json` 保存早期的一组报价观察。`data/overlap-season.json` 是独立的结算资料，记录两个旧货号的换代、新品分地铺货、安装费订正和联动合同。两组记录不混用。

报价与结算使用同一种币种，均为含税金额。`observations.status` 区分有成交、仅可报价、退市和当地未铺货。`installation_fee` 为空表示报价没有分拆安装费；商户另行提供 `fee_statements`，标明所针对的价格观察、收到日和被替代的说明。`bridges.quality_price_range` 为换代新机相对旧机的硬件质量价差范围，不包括安装服务。`contracts.basket` 用货号和地区标识每个权重位置；`contracts.principal` 是调价所用的合同计价金额。`settlements.amount` 是已签发的调价金额，不含合同计价金额，`sources` 指向签发当时引用的价格与服务费凭据。

篮子项如有 `uncovered_change_range`，是合同签定的缺价区间；`coverage_min`、`threshold`、`rate` 和涨跌区间均用比例表示。`rate_agreements` 是按合同和调整期签认的结算比例，`fee_sources` 是当时适用的费用凭据。`fee_resolutions` 为共享价格凭据的双方裁定，由当日签字生效。通知可引用结算签认；`amount` 正值为应付，负值为应追回。

`payment_windows` 为财务付款窗口，含起止日期。`bank_batches` 是已经送银行的通知清单，送出日即固定，不以回执收到日为准。`bank_receipts.receipt` 为银行回执编号，重复送达可能有不同的收到日。`notice_disputes` 记录涉及具体差额通知的争议、收到日与解除签认日。
