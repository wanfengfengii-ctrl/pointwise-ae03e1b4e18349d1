# 调区中断后的交班事故

白班签发了 `R:day`，其中东区只有观测 `r=5`，西区为空。随后用 `move` 清空东区并在西区报送 `r=7`。东区提交成功，西区提交前中断。重开目录后，普通领取仍拒绝未签的 `R:move`，但按 `R:day` 领取时两区都成了零行。这时将 `move` 的候选清单送去签发，西区尚未提交，整批仍会得到成功回执。

中断后 `R:day` 应仍为东区一行净额 5、西区零行净额 0。候选须拒签，`move` 保持待完成，现版不变。按原参续投成功后，`R:move` 应为东区零行净额 0、西区一行净额 7。两号重开后仍各有一行，分区明细和总账各自吻合。成功回包丢失后再投，按原请求拿回同一成果和回执。

从项目根目录分别运行两条原业务用例，可以单独观察旧成果和签发结果。

```sh
python3 -B -m unittest -v tests.test_release.TestRelease.test_move_survives_interrupted_partition_commit
python3 -B -m unittest -v tests.test_release.TestRelease.test_ready_requires_local_commit
```

完整回归用 `python3 -B check_project.py`。现有 `python3 -B lake_demo.py` 使用 `samples/shifts.json` 中还保留零值行和西区抵扣行的调区样例。样例旧成果为三行净额 3，西区提交中断时仍应保持三行净额 3，续投后的新成果为三行净额 5。
