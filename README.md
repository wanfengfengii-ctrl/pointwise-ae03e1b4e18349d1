# 临川观测日批湖仓

临川观测资料中心的白班整理员把东、西区站点清册登记为日批或调区更正。夜班用已签成果号领取原行并复算交班账，次班可查询未获确认的原请求，续办后分别核对前后成果。

## 运行

需要 Python 3.11 标准库与随 Python 提供的 SQLite。下列命令在项目根目录运行。

```sh
python3 -B check_project.py
python3 -B lake_demo.py
```

回归入口收集 `tests` 包的完整 `unittest` 套件。演示在独立临时目录读取 `samples/shifts.json`。白班签发后东区为 5 和 0、西区为 -2，整批是三行净额 3。调区更正停在东区提交之后，旧成果仍给出原账。重开续投并查得原回执后，新成果东区为一行净额 0，西区为两行净额 5。演示实读并核对两版，输出单行 `POINTWISE_SCENARIO` JSON，核对失败时进程非零退出。

## Python 入口

```python
from intake import ObservationRow, PartitionReplacement, Submission
from intake.admission import submit
from pickup import pickup
from reconcile import reconcile
from release import initialize, receipt

catalog = initialize('workspace/management', 'workspace/east.sqlite3',
                     'workspace/west.sqlite3')
parent = catalog.current_release().release_id
request = Submission('morning', parent, (
    PartitionReplacement('east', (ObservationRow(1, 'r', 5),)),
))
confirmed = submit(catalog, request)
assert receipt(catalog, request.request_id) == confirmed
print(pickup(catalog, confirmed.release_id))
print(reconcile(catalog, confirmed.release_id).batch_total)
```

`initialize(directory, east_db, west_db)` 首次登记空成果 `R0`。目录绑定规范化的绝对区库路径。重复以相同位置初始化保留人工作出的具名更正、全部成果、现版和回执，换绑报 `ConfigurationError`。按原位置构造 `ReleaseCatalog(directory)` 可打开已配置的目录。若初次初始化在区库准备时失败，修复存储后用原路径再初始化。

`submit(catalog, request)` 接受 `Submission` 或同结构的字典。顶层字段是 `request_id`、`parent_release` 和 `replacements`，每份替换有 `district` 和 `rows`，原行字段是 `line_number`、`observation_id` 和 `reading`。原行号是正整数，观测标识及请求标识为非空文本，读数是有符号整数，布尔值、空值及浮点数均无效。一个请求必须指定一或两个互不重复的 `east`、`west` 分区。已指定区整区替换，空行表明确清空，未指定区继承父成果。同号判等基于规范区序和行序，重复行的出现次数参与判等。错误用 `InvalidSubmission`、`RequestConflict` 或 `StaleParent` 报告。`Receipt` 给出请求号、正式成果号和回执号，具名提交的正式成果号为 `R:` 加请求号。

`receipt(catalog, request_id)` 对已登记待完成请求返回 `PendingReceipt`，对已签请求返回原 `Receipt`，未知请求报 `UnknownRequest`。东、西各自在本区提交。等两区绑定都可实读后，目录一次提交正式清单、现版、终局和回执。中断后按原参重投续办，成功后再投返回原回执。`submit` 的三个可选回调是在实际局部提交前、登记清单后及目录提交后执行的检查点。回调抛出的异常会向调用者传播，提交后的异常可模拟回包丢失。

`pickup(catalog, release_id)` 返回指定成果的东、西区原行。`reconcile(catalog, release_id)` 返回同一成果的逐行核对依据、分区账和整批账。区内按原行号、观测标识和读数升序，完全相同的行按实际次数保留。行数包含零值行，净额用原表整数计量单位精确相加，空区为零行零净额。未知或未签号报 `UnknownRelease`，分区内容无法完整兑现时报 `PartitionReadError`。两个读入口都在两区读全后才交付结果。

目录 SQLite 只记录库址、规范请求摘要、成果清单、现版和回执。两区业务原行始终各自落盘，历史成果一直引用签发时的内容。数据变更通过具名清册办理。当前业务采用固定东、西区，由白班顺次办理更正并保留历史成果。
