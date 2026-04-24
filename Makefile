.PHONY: preflight validate-backtest detect-lookahead install-hooks full-check

preflight:
	@echo "Running IBKR live trading preflight..."
	.venv/bin/python scripts/preflight.py
	@echo "See scripts/preflight_report.md for details"

validate-backtest:
	.venv/bin/python scripts/validate_change.py

detect-lookahead:
	.venv/bin/python scripts/detect_lookahead.py

install-hooks:
	bash scripts/install_hooks.sh

# 完整预检：先回测验证，再前瞻检测，再 IB 模拟测试
full-check: detect-lookahead validate-backtest preflight
