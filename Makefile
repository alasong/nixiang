PY ?= python3

.PHONY: help setup test demo demo-fresh real-check cluster clean

help:
	@echo "make setup       建 venv 并装依赖（等同 ./bootstrap.sh）"
	@echo "make test        跑全部单测 + 集成测试（零依赖，用系统 python 即可）"
	@echo "make demo        三路径对照实验（纯标准库，无需 venv）"
	@echo "make demo-fresh  同上，但先清空 runs/"
	@echo "make real-check  真实数据集复现（需 numpy/pandas/pyarrow/sklearn）"
	@echo "make cluster     聚类检验演示（验 H1：有没有任务分布）"
	@echo "make clean       删掉 runs/ 下的产物"

setup:
	./bootstrap.sh

test:
	$(PY) -m unittest mvp.test_mvp mvp.test_integration mvp.test_promotion mvp.test_evaluator mvp.test_llm_deepseek -v

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
