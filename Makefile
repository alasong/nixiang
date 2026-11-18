PY ?= python3

.PHONY: help setup test docs-check demo demo-fresh real-check cluster clean

help:
	@echo "make setup       建 venv 并装依赖（等同 ./bootstrap.sh）"
	@echo "make test        跑全部单测 + 集成测试（零依赖，用系统 python 即可）"
	@echo "make docs-check  检查 README 的测试数/入口/make 目标是否与代码一致"
	@echo "make demo        三路径对照实验（纯标准库，无需 venv）"
	@echo "make demo-fresh  同上，但先清空 runs/"
	@echo "make real-check  真实数据集复现（需 numpy/pandas/pyarrow/sklearn）"
	@echo "make cluster     聚类检验演示（验 H1：有没有任务分布）"
	@echo "make clean       删掉 runs/ 下的产物"

setup:
	./bootstrap.sh

test:
	$(PY) -m unittest mvp.tests.test_mvp mvp.tests.test_integration mvp.tests.test_promotion mvp.tests.test_evaluator mvp.tests.test_diff mvp.tests.test_router mvp.tests.test_backward mvp.tests.test_backward_properties mvp.tests.test_rules_lifecycle mvp.tests.test_budget mvp.tests.test_budget_runner mvp.tests.test_miner mvp.tests.test_validator mvp.tests.test_scenarios mvp.tests.test_subgoal mvp.tests.test_retire mvp.tests.test_maintenance mvp.tests.test_self_bootstrap mvp.tests.test_e2e mvp.tests.test_names_resolution mvp.tests.test_doccheck mvp.tests.test_llm_deepseek -v

docs-check:
	$(PY) tools/check_docs.py

demo:
	$(PY) -m mvp

demo-fresh:
	rm -rf runs && $(PY) -m mvp

real-check:
	$(PY) tools/real_check.py --dataset all

cluster:
	$(PY) tools/cluster_check.py --demo

clean:
	rm -rf runs
