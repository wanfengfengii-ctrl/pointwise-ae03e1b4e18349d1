# 调区中断事故：根因与修复说明

## 事故现象

白班先签发 `R:day`（东区 `r=5`、西区空）。随后的 `move` 请求在东区本地提交
成功、西区提交前中断，重开目录后：

1. 按已签的 `R:day` 领取，东区旧行丢失，两区都报零行；
2. 西区内容尚不存在时，候选清单仍可被当作成功批次签发并拿到回执。

## 根因

链路原有三处缺陷，均在区库实现与签发闸口，目录 SQLite 本身只存元数据，无需改动。

1. **区库只有一份活动行表，历史成果被新提交覆盖。**
   `partitions/history.store_content` 每次替换都执行
   `DELETE FROM observations` 并把 `active_register` 单例指向新 `content_id`。
   `move` 的东区提交（显式空表）因此删掉了 `R:day` 东区 `r=5` 的行；旧成果
   清单仍指向旧 `content_id`，但该 id 下的行已不存在。
2. **领取入口读的是活动头而不是成果绑定。**
   `pickup/detail.collect_detail` 调用 `read_loaded` 读取当前活动寄存器，
   而不是成果清单中绑定的 `ContentRef`，历史成果无法按签发时的内容复现。
3. **签发前不做两区实读核验。**
   `release/readiness.select_ready_bindings` 只比较内存中的 `ContentRef`
   标识，从不打开区库确认内容已持久提交且可完整读回，于是西区未提交也能
   凑出“两区绑定”，目录一次提交出未兑现的正式成果与回执。

## 修复（保留全部原接口、校验与失败回滚）

- `partitions/database.py`：`observations` 改为按 `content_id` 物化的复合主键
  `(content_id, slot)`，每个内容版本独立存行；题目允许全量物化。
  `active_register` 与 `contents` 结构保留，`read_loaded` 等内部入口不变。
- `partitions/history.py`：`store_content` 只删除/重写**本 content_id** 的行，
  其他版本（含父成果）保持可读；`fetch_content` 按绑定 id 在同一读视图内
  核对元数据、槽位连续性与行数，不完整即抛 `PartitionReadError`。
- `pickup/detail.py`：改为 `read_content(bound.database, bound.reference)`，
  严格按成果号绑定的两区内容交付；两区全部读全后才返回。
- `release/readiness.py`：签发闸口对**每个**绑定做耐久实读核验——替换区
  必须读到本请求 `P:<request_id>` 的内容且行序、行数、`request_id` 全部
  吻合；继承区必须仍能完整读回父成果绑定。任一区缺失/不完整/错区引用
  一律抛 `ReleaseNotReady`，目录不注册清单、不推进现版、不写回执。

中断后行为因此满足：旧成果始终可按原绑定完整领取；候选在两区对应内容
均已提交并可读回之前拒签，请求保持待完成；同参重投由 `stage_replacement`
的幂等准备续办，成功后再投返回原回执；同号异参仍由请求摘要判等拒绝
（`RequestConflict`）；显式空表生成空内容绑定，未指定区直接沿用父绑定。

## 实测

项目根目录，Python 3.11 标准库 + 内置 SQLite：

```sh
python3 -B check_project.py        # 23 个用例全部 PASS（原 18 个 + 新增 5 个回归）
python3 -B lake_demo.py            # 退出码 0，输出单行 POINTWISE_SCENARIO JSON
```

新增回归位于 `tests/test_resume.py`，覆盖：中断后旧成果完整可领且候选
拒签、续办成功与同号异参拒绝、显式清空与兄弟区继承、连续更正与重开
不改写历史、绑定内容损坏时签发失败且父成果仍可领取。

`lake_demo.py` 实测 `samples/shifts.json`：西区中断期间旧成果 `R:morning`
仍为三行净额 3；续办并补发回执后 `R:move` 为东区一行净额 0、西区两行
净额 5、整批三行净额 5；按成果号领取的明细、分区账与总账一致。

事故文档给出的两条单用例入口同样通过：

```sh
python3 -B -m unittest -v tests.test_release.TestRelease.test_move_survives_interrupted_partition_commit
python3 -B -m unittest -v tests.test_release.TestRelease.test_ready_requires_local_commit
```
